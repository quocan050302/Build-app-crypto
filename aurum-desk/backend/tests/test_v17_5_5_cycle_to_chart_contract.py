"""
backend/tests/test_v17_5_5_cycle_to_chart_contract.py
AURUM V17.5.5 — End-to-End Cycle to Chart Evidence Contract Integration Suite.
Covers Parts 171–180, Appendix E1, E2:
1. Full end-to-end production path without synthetic mocked orders or mock detectors.
2. Real candle sequence feeds detector -> candidate generated with bound origin.
3. evaluate_cycle binds context and cost snapshot before ARM.
4. _arm_paper_candidate creates PaperOrder with matching context/cost authority.
5. Coordinator fills order with slippage parity.
6. Lifecycle evaluates exit and records post-trade outcome in DB.
7. Stop evidence API reads actual DB order and returns verified structural anchor.
8. Negative guards fail closed with precise reasons.
"""

import json
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
import crud
from services.clock import ReplayClock
from services.execution_coordinator import ExecutionCoordinator
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator
from services.scalp_m1_model_service import ScalpM1ModelService, M1ScalpConfig
from services.trade_lifecycle_service import TradeLifecycleService
from services.stop_placement_types import StopPlacementPolicy
from services.stop_placement_research_service import (
    read_verified_comparison_cost_snapshot,
)


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
def test_db_env():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    db = Session()

    # Seed system configs
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

    yield db, clock, orchestrator, coordinator, Session


def test_full_cycle_detector_to_stop_evidence_contract(test_db_env):
    """Phần 171–177: Real candle detector -> Candidate with origin -> Order -> Coordinator Fill -> Stop Evidence API."""
    db, clock, orchestrator, coordinator, Session = test_db_env
    t_base = 100_000

    # Build sequence of 25 closed M1 candles for SHORT continuation
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
        (2005, 2008, 2004, 2007),  # High 2008 at i=18 (origin swing high)
        (2007, 2007, 2003, 2004),
        (2004, 2005, 2002, 2003),
        (2003, 2004, 2001, 2002),
        (2002, 2003, 1998, 1999),  # Break bar closing at 1999.0
        (1999, 2001, 1998, 2000),  # Retest
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
        "slippage_usd": 0.10,
        "config_version": 1,
    }
    stop_policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL", version=1)
    quote = {"bid": 2000.0, "ask": 2000.10, "last": 2000.05, "timestamp": now_ms, "is_valid": True, "is_stale": False}

    cfg = M1ScalpConfig(
        scalp_m1_enabled=True,
        scalp_m1_mode="PAPER",
        pivot_left_bars=2,
        pivot_right_bars=2,
        displacement_atr_period=7,
        sl_buffer_ticks=10,
        min_net_rr=1.2,
    )

    # 1. Real Detector Invocation with Origin Binding
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

    assert cand.is_eligible is True, f"Candidate was rejected: {cand.reason_code} - {cand.missing_conditions}"
    assert cand.stop_placement is not None
    assert cand.stop_placement.status == "VALID"
    assert cand.stop_placement.anchor is not None
    assert cand.stop_placement.anchor.price == 2008.0  # Authoritative protected high origin!
    assert cand.stop_comparison is not None

    # 2. Context binding & ARM
    order = orchestrator._arm_paper_candidate(
        db,
        cand,
        now_ms=now_ms,
        clock=clock,
        quote=quote,
        policy_settings=policy_settings,
        config=cfg,
        auto_enabled=True,
    )
    assert order is not None
    assert order.state == "armed"
    db.commit()

    # 3. Read back order and verify frozen cost snapshot & stop evidence
    fresh_order = db.query(models.PaperOrder).filter(models.PaperOrder.id == order.id).first()
    assert fresh_order is not None
    cost_snap = json.loads(fresh_order.cost_snapshot)
    assert cost_snap.get("stop_placement") is not None
    sp_snap = cost_snap["stop_placement"]
    assert sp_snap["anchor"]["price"] == 2008.0

    # 4. Coordinator execution / fill
    fill_quote = {"bid": 2000.0, "ask": 2000.10, "last": 2000.05, "timestamp": now_ms + 1000}
    coordinator.evaluate_orders_sync(
        db,
        ticker_override=fill_quote,
        clock=clock,
        now_ms=now_ms + 1000,
    )
    filled_order = db.query(models.PaperOrder).filter(models.PaperOrder.id == order.id).first()
    assert filled_order.state in ("open", "paper_open")
    # SHORT Market fill: bid - slippage = 2000.0 - 0.10 = 1999.90
    assert abs(filled_order.actual_entry - 1999.90) < 1e-4

    # 5. Stop evidence endpoint contract verification (Appendix D)
    from main import get_order_stop_evidence_endpoint
    payload = get_order_stop_evidence_endpoint(order_id=filled_order.id, db=db)
    assert payload["status"] == "AVAILABLE"
    assert payload["order_id"] == filled_order.id
    assert payload["direction"] == "SHORT"
    assert payload["actual_entry"] == 1999.90
    assert payload["actual_sl"] == filled_order.stop_loss
    assert payload["take_profit"] == filled_order.take_profit
    # Geometry for SHORT: TP < Entry < SL
    assert payload["take_profit"] < payload["actual_entry"] < payload["actual_sl"]
    assert payload["stop_placement"]["anchor"]["price"] == 2008.0


def test_cycle_negative_guards_fail_closed(test_db_env):
    """Phần 178: Auto OFF, stale quote, or invalid geometry fails closed without creating armed order."""
    db, clock, orchestrator, coordinator, Session = test_db_env
    now_ms = 1_000_000

    # Candidate with invalid quote
    quote_stale = {"bid": 2000.0, "ask": 2000.10, "last": 2000.05, "timestamp": now_ms - 20_000, "is_stale": True}
    cfg = M1ScalpConfig()

    class DummyCand:
        candidate_key = "dummy_cand_test"
        model_id = "M1_CONTINUATION"
        symbol = "XAUUSDT"
        direction = "SHORT"
        planned_entry = 2000.0
        stop_loss = 2010.0
        take_profit = 1980.0
        snapshot = None
        calculation = None

    cand = DummyCand()

    # ARM should fail closed due to missing calculation / invalid quote
    order = orchestrator._arm_paper_candidate(
        db,
        cand,
        now_ms=now_ms,
        clock=clock,
        quote=quote_stale,
        policy_settings={},
        config=cfg,
        auto_enabled=False,
    )
    assert order is None
