"""
Aurum V17.5.2 — Full Detector-to-Exit Acceptance Suite
PHẦN 131 - 140, PHẦN 175:
Verifies the end-to-end execution path starting from actual detector candle inputs,
generating actual model candidates, arming through real orchestrator._arm_paper_candidate,
filling through ExecutionCoordinator, exiting through TradeLifecycleService, and
recording post-trade review.
NO synthetic manual PaperOrder(state='armed') seeding!
"""
import json
import pytest
from types import SimpleNamespace
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
import crud
from services.clock import ReplayClock
from services.quote_validator import CanonicalQuote
from services.execution_coordinator import ExecutionCoordinator
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator
from services.scalp_m1_model_service import ScalpM1ModelService, M1ScalpConfig
from services.trade_lifecycle_service import TradeLifecycleService
from services.stop_placement_types import StopPlacementPolicy
from services.post_trade_review_service import classify_post_trade_lesson


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
def detector_env(tmp_path):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    db = Session()

    # Seed core configuration
    db.add(models.SystemConfig(key="timing_profile", value="CG01_ALL_SESSIONS_PAPER", updated_at=1000000))
    db.add(models.SystemConfig(key="execution_scope", value="PAPER", updated_at=1000000))
    db.add(models.SystemConfig(key="stop_placement_v1.mode", value="PAPER_STRUCTURAL", updated_at=1000000))
    db.add(models.SystemConfig(key="stop_placement_v1.version", value="1", updated_at=1000000))

    clock = ReplayClock(1_000_000)
    audit = crud.get_or_create_today_audit(db, clock=clock)
    audit.initial_equity = 5000.0
    audit.current_equity = 5000.0
    db.commit()

    orchestrator = ScalpWatcherOrchestrator()
    coordinator = ExecutionCoordinator()

    yield db, clock, orchestrator, coordinator
    db.close()


def test_full_detector_to_exit_short_continuation(detector_env):
    """
    PHẦN 131 - 138: Complete end-to-end integration for SHORT continuation:
    1. Closed M1 candles generate swing highs and a BOS break below previous swing low.
    2. ScalpM1ModelService.evaluate_m1_continuation_candidate creates candidate with Variant C stop.
    3. orchestrator._arm_paper_candidate arms the order (NO test-side PaperOrder creation!).
    4. ExecutionCoordinator fills the order on matching quote.
    5. TradeLifecycleService closes the order on Take Profit quote.
    6. Post trade review classifies lesson.
    """
    db, clock, orchestrator, coordinator = detector_env
    t_base = 100_000

    # Build sequence of 25 closed M1 candles:
    # 0..9: Warmup sideways candles around 2005
    # 10..15: Swing high at 2011.0 (bar 13) and swing low at 2002.0 (bar 16)
    # 16..20: Pullback up to 2008.0 (origin swing high before break)
    # 21..24: Breakout bar closing at 1999.0 (below 2002.0) and execution bar
    warmup_prices = [(2004 + (i % 3), 2007, 2003, 2005) for i in range(10)]
    pattern_prices = [
        (2005, 2007, 2004, 2006),  # 10
        (2006, 2009, 2005, 2008),  # 11
        (2008, 2010, 2007, 2009),  # 12
        (2009, 2011, 2008, 2010),  # 13: High 2011
        (2010, 2010, 2006, 2007),  # 14
        (2007, 2008, 2003, 2004),  # 15
        (2004, 2005, 2001, 2002),  # 16: Low 2001, close 2002 (Support)
        (2002, 2006, 2002, 2005),  # 17
        (2005, 2008, 2004, 2007),  # 18: High 2008 (Origin pivot protecting break)
        (2007, 2007, 2003, 2004),  # 19
        (2004, 2005, 2002, 2003),  # 20
        (2003, 2004, 2001, 2002),  # 21
        (2002, 2003, 1998, 1999),  # 22: Break bar closing at 1999.0 (< 2002.0)
        (1999, 2001, 1998, 2000),  # 23: Retest/Execution bar
    ]
    prices = warmup_prices + pattern_prices
    candles = []
    for i, (o, h, l, c) in enumerate(prices):
        t_open = t_base + i * 60_000
        candles.append(CandleBar(t_open, o, h, l, c))

    now_ms = t_base + len(prices) * 60_000
    clock.set_time(now_ms)

    policy_settings = {
        "capital_usdt": 5000.0,
        "risk_pct": 0.10,
        "leverage": 10,
        "margin_mode": "ISOLATED",
    }
    stop_policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL", version=1)

    quote = {
        "bid": 2000.0,
        "ask": 2000.10,
        "last": 2000.05,
        "timestamp": now_ms,
        "is_valid": True,
        "is_stale": False,
    }

    # 1. ACTUAL DETECTOR INVOCATION
    cfg = M1ScalpConfig(
        pivot_left_bars=2,
        pivot_right_bars=2,
        displacement_atr_period=7,
        sl_buffer_ticks=10,
        min_net_rr=1.2,
    )
    cand = ScalpM1ModelService.evaluate_m1_continuation_candidate(
        candles_1m=candles,
        candles_5m=[],
        current_quote=quote,
        htf_bias="BEARISH",
        h1_alignment="ALIGNED",
        policy_settings=policy_settings,
        now_ms=now_ms,
        config=cfg,
        stop_policy=stop_policy,
    )

    assert cand.is_eligible, f"Detector candidate should be eligible: {cand.missing_conditions}"
    assert cand.direction == "SHORT"
    assert cand.stop_placement is not None
    assert cand.stop_placement.applied_variant == "C"
    # Structural stop must be placed above the origin high (2008.0)
    assert cand.stop_loss > 2008.0

    # 2. ACTUAL ORCHESTRATOR ARM INVOCATION (Not test-side manual PaperOrder!)
    armed_order = orchestrator._arm_paper_candidate(
        db,
        cand,
        now_ms=now_ms,
        clock=clock,
        quote=quote,
        policy_settings=policy_settings,
        config=cfg,
        auto_enabled=True,
    )

    assert armed_order is not None, "Real orchestrator must arm candidate"
    assert armed_order.state == "armed"
    assert armed_order.direction == "SHORT"
    assert armed_order.stop_loss == cand.stop_loss
    db.commit()

    # 3. ACTUAL COORDINATOR FILL INVOCATION
    fill_quote = {
        "bid": 2000.0,
        "ask": 2000.10,
        "last": 2000.05,
        "exchange_time": now_ms + 1000,
        "observed_at": now_ms + 1000,
        "is_valid": True,
        "is_stale": False,
    }
    coordinator.evaluate_orders_sync(
        db=db,
        ticker_override=fill_quote,
        clock=clock,
        now_ms=now_ms + 1000,
    )
    db.commit()

    order = db.query(models.PaperOrder).filter_by(id=armed_order.id).one()
    assert order.state == "paper_open"
    assert abs(order.actual_entry - 2000.0) <= 0.20

    # 4. ACTUAL LIFECYCLE EXIT INVOCATION (Take Profit Hit)
    tp_quote = {
        "bid": order.take_profit - 1.0,
        "ask": order.take_profit - 0.9,
        "last": order.take_profit - 1.0,
        "exchange_ts_ms": now_ms + 10000,
        "timestamp": now_ms + 10000,
    }
    closed = TradeLifecycleService.process_exit_tick(
        db=db,
        current_bid=tp_quote["bid"],
        current_ask=tp_quote["ask"],
        now_ms=now_ms + 10000,
        clock=clock,
        tick_timestamp=tp_quote["exchange_ts_ms"],
    )
    assert closed is not None
    assert closed.id == order.id
    assert closed.state == "closed"
    assert closed.exit_cause == "TP_HIT"
    db.commit()

    # 5. ACTUAL POST-TRADE REVIEW CLASSIFICATION
    from services.post_trade_review_service import build_post_trade_review, EntryComplianceResult
    rev = build_post_trade_review(order=closed)
    comp = EntryComplianceResult(status="VERIFIED_COMPLIANT", reason="Kế hoạch hợp lệ", missing_evidence=[])
    entry_snap = json.loads(closed.cost_snapshot) if closed.cost_snapshot else {}
    classification = classify_post_trade_lesson(review=rev, entry_snapshot=entry_snap, compliance_result=comp)
    assert classification is not None
    assert classification.suggested_color == "GREEN"
    assert rev.gross_pnl > 0.0


def test_full_detector_to_exit_long_continuation(detector_env):
    """
    PHẦN 138: Complete end-to-end integration for LONG continuation:
    Symmetrical path verifying LONG directional geometry:
    SL < Entry < TP, Bid exit triggers, and structural anchor below protective low.
    """
    db, clock, orchestrator, coordinator = detector_env
    t_base = 200_000

    # Symmetrical BULLISH sequence
    # Resistance at 2008.0, origin protective low at 2001.0, breakout close at 2010.0
    candles = []
    warmup_prices = [(2004 + (i % 3), 2007, 2003, 2005) for i in range(10)]
    pattern_prices = [
        (2005, 2006, 2004, 2005),
        (2005, 2008, 2004, 2008),  # Resistance at 2008
        (2008, 2008, 2002, 2003),
        (2003, 2004, 2000, 2001),  # Origin Low at 2000
        (2001, 2006, 2001, 2005),
        (2005, 2008, 2004, 2007),
        (2007, 2011, 2006, 2010),  # Breakout bar closing at 2010 > 2008
        (2010, 2012, 2009, 2011),  # Execution bar
    ]
    prices = warmup_prices + pattern_prices

    for i, (o, h, l, c) in enumerate(prices):
        t_open = t_base + i * 60_000
        candles.append(CandleBar(t_open, o, h, l, c))

    now_ms = t_base + len(prices) * 60_000
    clock.set_time(now_ms)

    policy_settings = {
        "capital_usdt": 5000.0,
        "risk_pct": 0.10,
        "leverage": 10,
        "margin_mode": "ISOLATED",
    }
    stop_policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL", version=1)

    quote = {
        "bid": 2011.0,
        "ask": 2011.10,
        "last": 2011.05,
        "timestamp": now_ms,
        "is_valid": True,
        "is_stale": False,
    }

    cfg = M1ScalpConfig(
        pivot_left_bars=2,
        pivot_right_bars=2,
        displacement_atr_period=7,
        sl_buffer_ticks=10,
        min_net_rr=1.2,
    )
    cand = ScalpM1ModelService.evaluate_m1_continuation_candidate(
        candles_1m=candles,
        candles_5m=[],
        current_quote=quote,
        htf_bias="BULLISH",
        h1_alignment="ALIGNED",
        policy_settings=policy_settings,
        now_ms=now_ms,
        config=cfg,
        stop_policy=stop_policy,
    )

    assert cand.is_eligible, f"LONG candidate should be eligible: {cand.missing_conditions}"
    assert cand.direction == "LONG"
    # LONG geometry: stop_loss < planned_entry < take_profit
    assert cand.stop_loss < cand.planned_entry < cand.take_profit
    # Structural stop must be below origin low (2000.0)
    assert cand.stop_loss < 2001.0

    # Arm candidate through orchestrator
    armed_order = orchestrator._arm_paper_candidate(
        db,
        cand,
        now_ms=now_ms,
        clock=clock,
        quote=quote,
        policy_settings=policy_settings,
        config=cfg,
        auto_enabled=True,
    )
    assert armed_order is not None
    assert armed_order.direction == "LONG"
    db.commit()

    # Fill order
    fill_quote = {
        "bid": 2011.0,
        "ask": 2011.10,
        "last": 2011.05,
        "exchange_time": now_ms + 1000,
        "observed_at": now_ms + 1000,
        "is_valid": True,
        "is_stale": False,
    }
    coordinator.evaluate_orders_sync(
        db=db,
        ticker_override=fill_quote,
        clock=clock,
        now_ms=now_ms + 1000,
    )
    db.commit()

    order = db.query(models.PaperOrder).filter_by(id=armed_order.id).one()
    assert order.state == "paper_open"

    # Close order on Stop Loss (LONG exits on executable BID <= SL)
    sl_quote = {
        "bid": armed_order.stop_loss - 0.50,
        "ask": armed_order.stop_loss - 0.40,
        "last": armed_order.stop_loss - 0.50,
        "exchange_ts_ms": now_ms + 10000,
        "timestamp": now_ms + 10000,
    }
    closed = TradeLifecycleService.process_exit_tick(
        db=db,
        current_bid=sl_quote["bid"],
        current_ask=sl_quote["ask"],
        now_ms=now_ms + 10000,
        clock=clock,
        tick_timestamp=sl_quote["exchange_ts_ms"],
    )
    assert closed is not None
    assert closed.state == "closed"
    assert closed.exit_cause == "SL_HIT"
    db.commit()


def test_negative_full_flow_guards(detector_env):
    """
    PHẦN 140: Negative verification:
    - Auto OFF prevents arming.
    - Missing structural decision in PAPER_STRUCTURAL mode prevents arming.
    """
    db, clock, orchestrator, coordinator = detector_env
    t_now = 500_000

    cand = SimpleNamespace(
        candidate_key="cand_negative_test",
        direction="SHORT",
        planned_entry=2000.0,
        stop_loss=2010.0,
        take_profit=1980.0,
        calculation=SimpleNamespace(is_valid=True, can_execute=True, stop_loss=2010.0),
        snapshot=None,
        stop_placement=None,
    )
    quote = {"bid": 2000.0, "ask": 2000.10, "last": 2000.05, "timestamp": t_now, "is_valid": True, "is_stale": False}

    # 1. Auto disabled: must return None
    res_auto_off = orchestrator._arm_paper_candidate(
        db,
        cand,
        now_ms=t_now,
        clock=clock,
        quote=quote,
        policy_settings={"capital_usdt": 5000.0, "risk_pct": 0.10},
        auto_enabled=False,
    )
    assert res_auto_off is None, "Auto disabled must prevent arming"

    # 2. Structural mode with missing decision: must fail closed
    res_missing_sp = orchestrator._arm_paper_candidate(
        db,
        cand,
        now_ms=t_now,
        clock=clock,
        quote=quote,
        policy_settings={"capital_usdt": 5000.0, "risk_pct": 0.10},
        auto_enabled=True,
    )
    assert res_missing_sp is None, "Missing structural decision must fail closed at ARM"
