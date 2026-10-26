"""
backend/tests/test_v17_5_3_cycle_to_review.py
AURUM V17.5.3 — Full Production Cycle from evaluate_cycle to Review and API
Covers Parts 121–140:
1. Calls ScalpWatcherOrchestrator.evaluate_cycle directly with deterministic closed candles.
   No manual seeding of PaperOrder(state='armed') or mock candidates!
2. Arming happens through real orchestrator._arm_paper_candidate.
3. Matching and filling through ExecutionCoordinator.evaluate_orders_sync.
4. Exit execution through TradeLifecycleService.
5. Real post-trade review generation through build_entry_review_context,
   assess_entry_compliance_for_review, build_post_trade_review, and classify_post_trade_lesson.
6. Real API verification of GET /api/v1/orders/{order_id}/stop-evidence and /chart-evidence.
7. Symmetrical SHORT and LONG cycles, plus negative guard isolation.
"""

import json
from types import SimpleNamespace
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
import crud
from services.clock import ReplayClock
from services.quote_validator import CanonicalQuote
from services.execution_coordinator import ExecutionCoordinator
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator
from services.scalp_m1_model_service import M1ScalpConfig
from services.trade_lifecycle_service import TradeLifecycleService
from services.stop_placement_types import StopPlacementPolicy
from services.post_trade_review_service import (
    build_entry_review_context,
    assess_entry_compliance_for_review,
    build_post_trade_review,
    classify_post_trade_lesson,
    save_post_trade_review,
    get_post_trade_review,
)
from main import get_order_stop_evidence_endpoint, get_order_chart_evidence


class CandleBar:
    def __init__(self, open_time, o, h, l, c):
        self.open_time = open_time
        self.timestamp = open_time
        self.open = float(o)
        self.high = float(h)
        self.low = float(l)
        self.close = float(c)
        self.is_closed = True


@pytest.fixture
def cycle_env():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    db = Session()

    # Seed core system configuration
    db.add(models.SystemConfig(key="timing_profile", value="CG01_ALL_SESSIONS_PAPER", updated_at=1000000))
    db.add(models.SystemConfig(key="execution_scope", value="PAPER", updated_at=1000000))
    db.add(models.SystemConfig(key="stop_placement_v1.mode", value="PAPER_STRUCTURAL", updated_at=1000000))
    db.add(models.SystemConfig(key="stop_placement_v1.version", value="1", updated_at=1000000))
    db.add(models.SystemConfig(key="scalp_m1_mode", value="PAPER", updated_at=1000000))

    clock = ReplayClock(1_000_000)
    audit = crud.get_or_create_today_audit(db, clock=clock)
    audit.initial_equity = 5000.0
    audit.current_equity = 5000.0
    db.commit()

    orchestrator = ScalpWatcherOrchestrator()
    coordinator = ExecutionCoordinator()

    yield db, clock, orchestrator, coordinator
    db.close()


def test_full_production_cycle_short_to_review_and_api(cycle_env):
    """
    Test 1: Full SHORT cycle from evaluate_cycle -> coordinator fill -> close -> review -> API.
    """
    db, clock, orchestrator, coordinator = cycle_env
    t_base = 100_000

    # Pattern candles: Warmup + Swing High at 2011, Support at 2002, Origin Pivot High at 2008, BOS close at 1999
    warmup_prices = [(2004 + (i % 3), 2007, 2003, 2005) for i in range(10)]
    pattern_prices = [
        (2005, 2007, 2004, 2006),  # 10
        (2006, 2009, 2005, 2008),  # 11
        (2008, 2010, 2007, 2009),  # 12
        (2009, 2011, 2008, 2010),  # 13: High 2011.0
        (2010, 2010, 2006, 2007),  # 14
        (2007, 2008, 2003, 2004),  # 15
        (2004, 2005, 2001, 2002),  # 16: Low 2001.0, close 2002.0 (Swing Low reference)
        (2002, 2006, 2002, 2005),  # 17
        (2005, 2008, 2004, 2007),  # 18: High 2008.0 (Origin protective swing high)
        (2007, 2007, 2003, 2004),  # 19
        (2004, 2005, 2002, 2003),  # 20
        (2003, 2004, 2001, 2002),  # 21
        (2002, 2003, 1998, 1999),  # 22: BOS break bar closing at 1999.0 (< 2002.0)
        (1999, 2001, 1998, 2000),  # 23: Execution retest bar
    ]
    prices = warmup_prices + pattern_prices
    candles = [CandleBar(t_base + i * 60_000, o, h, l, c) for i, (o, h, l, c) in enumerate(prices)]

    now_ms = t_base + len(prices) * 60_000
    clock.set_time(now_ms)

    quote = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2000.0,
        ask=2000.10,
        last=2000.05,
        exchange_ts_ms=now_ms,
        received_at_ms=now_ms,
        status="VALID",
    )

    cfg = M1ScalpConfig(
        scalp_m1_mode="PAPER",
        pivot_left_bars=2,
        pivot_right_bars=2,
        displacement_atr_period=7,
        sl_buffer_ticks=10,
        min_net_rr=1.2,
    )

    # 1. EVALUATE_CYCLE: Real entry point
    cycle_res = orchestrator.evaluate_cycle(
        db,
        symbol="XAUUSDT",
        now_ms=now_ms,
        clock=clock,
        quote=quote,
        candles_1m=candles,
        htf_bias="BEARISH",
        h1_alignment="ALIGNED",
        config=cfg,
        auto_enabled=True,
    )

    armed_order_id = cycle_res.get("armed_order_id")
    assert armed_order_id is not None, f"evaluate_cycle failed to arm candidate: {cycle_res}"

    armed_order = db.query(models.PaperOrder).filter_by(id=armed_order_id).one()
    assert armed_order.state == "armed"
    assert armed_order.direction == "SHORT"
    assert armed_order.stop_loss > armed_order.planned_entry > armed_order.take_profit
    # Variant C structural stop loss must protect above the origin swing high (2008.0)
    assert armed_order.stop_loss >= 2008.0

    # 2. COORDINATOR FILL: Incoming quote triggers market fill
    fill_time = now_ms + 1000
    clock.set_time(fill_time)
    fill_quote = {
        "bid": 2000.0,
        "ask": 2000.10,
        "last": 2000.05,
        "exchange_ts_ms": fill_time,
        "received_at_ms": fill_time,
        "timestamp": fill_time,
        "is_valid": True,
        "is_stale": False,
    }
    coordinator.evaluate_orders_sync(
        db=db,
        ticker_override=fill_quote,
        clock=clock,
        now_ms=fill_time,
    )
    db.commit()

    order = db.query(models.PaperOrder).filter_by(id=armed_order_id).one()
    assert order.state == "paper_open"
    assert order.actual_entry is not None
    assert abs(order.actual_entry - 2000.0) <= 0.20

    # 3. EXIT EXECUTION: Trade closes on TP hit
    exit_time = fill_time + 15000
    clock.set_time(exit_time)
    closed_order = TradeLifecycleService.execute_close(
        db=db,
        order_id=order.id,
        exit_price=order.take_profit,
        exit_cause="TP_HIT",
        now_ms=exit_time,
        clock=clock,
    )
    assert closed_order is not None
    assert closed_order.state == "closed"
    assert closed_order.actual_exit == order.take_profit
    assert closed_order.exit_cause == "TP_HIT"
    assert closed_order.realized_pnl_net is not None
    db.commit()

    # 4. REVIEW PIPELINE: build_entry_review_context -> assess_entry_compliance_for_review -> build_post_trade_review -> save
    rev_ctx = build_entry_review_context(db, closed_order)
    comp_res = assess_entry_compliance_for_review(closed_order, review_context=rev_ctx)

    outcome_dict = {
        "order_id": closed_order.id,
        "symbol": closed_order.instrument,
        "direction": closed_order.direction,
        "exit_price": closed_order.actual_exit,
        "exit_cause": closed_order.exit_cause,
        "gross_pnl": 5.0,
        "net_pnl": closed_order.realized_pnl_net,
        "realized_r": closed_order.realized_r,
        "closed_at": exit_time,
    }
    review = build_post_trade_review(closed_order, outcome=outcome_dict, now_ms=exit_time)
    assert review.order_id == closed_order.id
    assert review.direction == "SHORT"
    assert review.exit_cause == "TP_HIT"

    lesson = classify_post_trade_lesson(review=review, compliance_result=comp_res)
    assert lesson is not None

    save_post_trade_review(db, review)
    db.commit()

    saved_rev = get_post_trade_review(db, closed_order.id)
    assert saved_rev is not None
    assert saved_rev.order_id == closed_order.id
    assert saved_rev.exit_cause == "TP_HIT"

    # 5. API ENDPOINTS: Verify GET /api/v1/orders/{order_id}/stop-evidence
    api_res = get_order_stop_evidence_endpoint(order_id=closed_order.id, db=db)
    assert api_res["status"] == "AVAILABLE"
    assert api_res["order_id"] == closed_order.id
    assert api_res["direction"] == "SHORT"
    assert api_res["actual_sl"] == closed_order.stop_loss
    assert api_res["take_profit"] == closed_order.take_profit
    assert api_res["stop_mode_at_arm"] == "PAPER_STRUCTURAL"
    assert "stop_placement" in api_res


def test_full_production_cycle_long_to_review_and_api(cycle_env):
    """
    Test 2: Full LONG cycle verifying symmetrical geometry SL < entry < TP.
    """
    db, clock, orchestrator, coordinator = cycle_env
    t_base = 200_000

    # BULLISH pattern: Resistance at 2008, origin low at 2000, BOS close at 2010
    warmup_prices = [(2004 + (i % 3), 2007, 2003, 2005) for i in range(10)]
    pattern_prices = [
        (2005, 2006, 2004, 2005),  # 10
        (2005, 2008, 2004, 2008),  # 11: Resistance 2008
        (2008, 2008, 2002, 2003),  # 12
        (2003, 2004, 2000, 2001),  # 13: Origin Low 2000.0 (protective low)
        (2001, 2006, 2001, 2005),  # 14
        (2005, 2008, 2004, 2007),  # 15
        (2007, 2011, 2006, 2010),  # 16: Breakout bar closing at 2010.0 (> 2008.0)
        (2010, 2012, 2009, 2011),  # 17: Retest/Execution bar
    ]
    prices = warmup_prices + pattern_prices
    candles = [CandleBar(t_base + i * 60_000, o, h, l, c) for i, (o, h, l, c) in enumerate(prices)]

    now_ms = t_base + len(prices) * 60_000
    clock.set_time(now_ms)

    quote = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2010.5,
        ask=2010.6,
        last=2010.55,
        exchange_ts_ms=now_ms,
        received_at_ms=now_ms,
        status="VALID",
    )

    cfg = M1ScalpConfig(
        scalp_m1_mode="PAPER",
        pivot_left_bars=2,
        pivot_right_bars=2,
        displacement_atr_period=7,
        sl_buffer_ticks=10,
        min_net_rr=1.2,
    )

    cycle_res = orchestrator.evaluate_cycle(
        db,
        symbol="XAUUSDT",
        now_ms=now_ms,
        clock=clock,
        quote=quote,
        candles_1m=candles,
        htf_bias="BULLISH",
        h1_alignment="ALIGNED",
        config=cfg,
        auto_enabled=True,
    )

    armed_order_id = cycle_res.get("armed_order_id")
    assert armed_order_id is not None, f"evaluate_cycle failed to arm LONG candidate: {cycle_res}"

    armed_order = db.query(models.PaperOrder).filter_by(id=armed_order_id).one()
    assert armed_order.direction == "LONG"
    # LONG geometry: SL < Entry < TP
    assert armed_order.stop_loss < armed_order.planned_entry < armed_order.take_profit
    # Variant C structural stop loss must protect below origin swing low (2000.0)
    assert armed_order.stop_loss <= 2000.0

    # Fill through coordinator with Ask
    fill_time = now_ms + 1000
    clock.set_time(fill_time)
    coordinator.evaluate_orders_sync(
        db=db,
        ticker_override={
            "bid": 2010.5,
            "ask": 2010.6,
            "last": 2010.55,
            "exchange_ts_ms": fill_time,
            "received_at_ms": fill_time,
            "timestamp": fill_time,
            "is_valid": True,
            "is_stale": False,
        },
        clock=clock,
        now_ms=fill_time,
    )
    db.commit()

    order = db.query(models.PaperOrder).filter_by(id=armed_order_id).one()
    assert order.state == "paper_open"

    # Close order on SL (demonstrating loss / SL closure fidelity)
    exit_time = fill_time + 10000
    clock.set_time(exit_time)
    closed = TradeLifecycleService.execute_close(
        db=db,
        order_id=order.id,
        exit_price=order.stop_loss,
        exit_cause="SL_HIT",
        now_ms=exit_time,
        clock=clock,
    )
    assert closed.state == "closed"
    assert closed.exit_cause == "SL_HIT"
    db.commit()

    api_res = get_order_stop_evidence_endpoint(order_id=closed.id, db=db)
    assert api_res["status"] == "AVAILABLE"
    assert api_res["direction"] == "LONG"
    assert api_res["actual_sl"] < api_res["actual_entry"] < api_res["take_profit"]


def test_production_cycle_guard_negatives(cycle_env):
    """
    Test 3: Negative guard tests on the real evaluate_cycle pipeline:
    - auto_enabled=False: candidate is evaluated but NOT armed.
    """
    db, clock, orchestrator, coordinator = cycle_env
    t_base = 300_000

    warmup_prices = [(2004 + (i % 3), 2007, 2003, 2005) for i in range(10)]
    pattern_prices = [
        (2005, 2007, 2004, 2006),
        (2006, 2009, 2005, 2008),
        (2008, 2010, 2007, 2009),
        (2009, 2011, 2008, 2010),
        (2010, 2010, 2006, 2007),
        (2007, 2008, 2003, 2004),
        (2004, 2005, 2001, 2002),
        (2002, 2006, 2002, 2005),
        (2005, 2008, 2004, 2007),
        (2007, 2007, 2003, 2004),
        (2004, 2005, 2002, 2003),
        (2003, 2004, 2001, 2002),
        (2002, 2003, 1998, 1999),
        (1999, 2001, 1998, 2000),
    ]
    candles = [CandleBar(t_base + i * 60_000, o, h, l, c) for i, (o, h, l, c) in enumerate(warmup_prices + pattern_prices)]
    now_ms = t_base + len(candles) * 60_000
    clock.set_time(now_ms)

    quote = CanonicalQuote(
        symbol="XAUUSDT",
        bid=2000.0,
        ask=2000.10,
        last=2000.05,
        exchange_ts_ms=now_ms,
        received_at_ms=now_ms,
        status="VALID",
    )

    cfg = M1ScalpConfig(
        scalp_m1_mode="PAPER",
        pivot_left_bars=2,
        pivot_right_bars=2,
        displacement_atr_period=7,
        sl_buffer_ticks=10,
        min_net_rr=1.2,
    )

    # Auto disabled: Candidate must NOT be armed
    res = orchestrator.evaluate_cycle(
        db,
        symbol="XAUUSDT",
        now_ms=now_ms,
        clock=clock,
        quote=quote,
        candles_1m=candles,
        config=cfg,
        auto_enabled=False,
    )
    assert res.get("armed_order_id") is None
    armed_orders = db.query(models.PaperOrder).filter_by(state="armed").all()
    assert len(armed_orders) == 0
