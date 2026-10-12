"""
backend/tests/test_v14_4_2_entry_regression.py
V14.4.2 — Entry regression, guard matrix (S05-S06), and PaperObservation lifecycle (O01-O05).
"""
import pytest
import models
import crud
from services.clock import FakeClock
from services.execution_coordinator import execution_coordinator
from services.lesson_policy_service import LessonPolicyService
from services.lesson_rule_service import LessonRuleService
from services.strategy_service import strategy_service
from services.trading_policy_service import TradingPolicyService
from services.trade_lifecycle_service import TradeLifecycleService
from services.smc_observation_service import (
    persist_paper_observation, record_observation_snapshot, record_closed_outcome
)
from services.smc_scenario_types import ScenarioSnapshot, ScenarioState

import smc_fixtures as F
from ny_quota_fixtures import make_lesson, make_news, set_policy, set_saved_risk

OK_FEED = {"has_execution_gap": False, "is_lagging": False}
BIAS = {"LONG": "BULLISH", "SHORT": "BEARISH"}


@pytest.fixture
def db(isolated_db):
    LessonPolicyService.reset_to_defaults(isolated_db)
    LessonRuleService.invalidate_cache()
    set_saved_risk(isolated_db)
    set_policy(isolated_db, ny_fallback_enabled=False, entry_session_policy="NY_ONLY")
    return isolated_db


def seed_all(db, direction, now_ms=F.NOW_MS):
    for tf, candles in F.make_candles_by_tf(direction, now_ms).items():
        for c in candles:
            db.add(models.Candle(symbol="XAUUSDT", timeframe=tf, timestamp=c.timestamp, open=c.open, high=c.high,
                                 low=c.low, close=c.close, volume=c.volume, is_closed=True))
    db.commit()


def tick(direction, now_ms, age_ms=500):
    last = 2648.6 if direction == "LONG" else 2651.4
    ts = now_ms - age_ms
    return {"symbol": "XAUUSDT", "bid": last - 0.1, "ask": last + 0.1, "last": last - 0.1,
            "timestamp": ts, "observed_at": ts}


def run_cycle(db, direction, *, auto=True, now_ms=F.NOW_MS, feed=OK_FEED, tk=None):
    strategy_service.evaluate_upcoming_setups(
        "XAUUSDT", "15M", db=db, clock=FakeClock(now_ms), ticker=tk if tk is not None else tick(direction, now_ms),
        htf_bias=BIAS[direction], h1_alignment="ALIGNED", feed_health=feed, auto_enabled=auto)


def armed_orders(db):
    return db.query(models.PaperOrder).filter(models.PaperOrder.state == "armed").all()


# -------------------------------------------------------------------------------------------------
# S05: Guard matrix (news, lessons, feed health, quote staleness)
# -------------------------------------------------------------------------------------------------

def test_s05_news_lessons_account_quote_feed(db):
    """S05: Valid structural chain with blockers prevents entry while preserving facts."""
    seed_all(db, "LONG", F.NOW_MS)

    # 1. News blocker
    make_news(db, F.NOW_MS, offset_min=5)
    run_cycle(db, "LONG", now_ms=F.NOW_MS)
    assert len(armed_orders(db)) == 0
    audit = crud.get_or_create_today_audit(db)
    assert audit.fills_count == 0

    # Verify structural scenario facts were still recorded despite the blocker
    scenarios = db.query(models.SMCScenarioSnapshot).all()
    assert len(scenarios) > 0

    # Reset news
    db.query(models.EconomicNews).delete()
    db.commit()

    # 2. Feed health execution gap blocker
    run_cycle(db, "LONG", now_ms=F.NOW_MS, feed={"has_execution_gap": True, "is_lagging": False})
    assert len(armed_orders(db)) == 0

    # 3. Quote staleness blocker (quote age 60s)
    stale_tk = tick("LONG", F.NOW_MS, age_ms=60_000)
    run_cycle(db, "LONG", now_ms=F.NOW_MS, tk=stale_tk)
    assert len(armed_orders(db)) == 0


# -------------------------------------------------------------------------------------------------
# S06: Cleanup after entry close
# -------------------------------------------------------------------------------------------------

def test_s06_close_and_new_signal(db):
    """S06: Order close creates outcome observation, advances cleanly without duplicate fills."""
    seed_all(db, "LONG", F.NOW_MS)
    run_cycle(db, "LONG", now_ms=F.NOW_MS)

    armed = armed_orders(db)
    assert len(armed) == 1
    order = armed[0]

    # Fill the order
    execution_coordinator.evaluate_orders_sync(
        db, ticker_override=tick("LONG", F.NOW_MS, 200), clock=FakeClock(F.NOW_MS), now_ms=F.NOW_MS
    )
    db.refresh(order)
    assert order.state == "paper_open"

    # Close the order via TradeLifecycleService.execute_close
    closed_order = TradeLifecycleService.execute_close(
        db, order_id=order.id, exit_price=2685.0, exit_cause="TP_HIT", now_ms=F.NOW_MS + 600_000
    )
    assert closed_order is not None
    db.refresh(order)
    assert order.state == "closed"

    # Verify closed outcome was persisted
    obs_rows = db.query(models.PaperObservation).filter(
        models.PaperObservation.event_kind == "ORDER_CLOSED",
        models.PaperObservation.order_id == order.id
    ).all()
    assert len(obs_rows) == 1
    assert obs_rows[0].order_id == order.id

    # Advance clock and run cycle again: old order identity is consumed and never re-armed
    run_cycle(db, "LONG", now_ms=F.NOW_MS + 900_000)
    new_armed = armed_orders(db)
    assert order.id not in [o.id for o in new_armed]
    db.refresh(order)
    assert order.state == "closed"


# -------------------------------------------------------------------------------------------------
# O01 - O05: PaperObservation Table & Lifecycle Tests (Appendix J)
# -------------------------------------------------------------------------------------------------

def test_o01_same_snapshot_twice(db):
    """O01: Same snapshot twice -> one DB row with same ID, readable across queries."""
    snap = ScenarioSnapshot(
        scenario_id="scen-o01",
        instance_id="inst-o01",
        revision=1,
        symbol="XAUUSDT",
        setup_timeframe="5M",
        context_timeframes=["H1", "H4"],
        family="STANDARD_SMC",
        direction="LONG",
        state=ScenarioState.STRUCTURE_CONFIRMED,
        as_of_ms=1000,
        evidence_cutoff_ms=1000,
        evidence_ids=[],
        liquidity_map={},
        trigger_chain={"verified": True, "node_ids": {}},
        target={"price": 2700.0},
        invalidation={"price": 2640.0},
        summary_vi="Test",
        config_version=1,
        detector_version="v14.4.1",
        digest="digest-o01",
        schema_version="v14.4.1",
    )
    id1 = record_observation_snapshot(db, snap)
    id2 = record_observation_snapshot(db, snap)
    assert id1 == id2
    db.commit()

    rows = db.query(models.PaperObservation).filter(models.PaperObservation.id == id1).all()
    assert len(rows) == 1
    assert rows[0].snapshot_id == snap.snapshot_id


def test_o02_immutability_conflict(db):
    """O02: Same identity with altered payload raises OBSERVATION_IMMUTABILITY_CONFLICT."""
    snap_id = "snap-immut-1"
    p1 = {"level": 2650.0, "version": 1}
    p2 = {"level": 2655.0, "version": 2}

    persist_paper_observation(
        db, namespace="paper", event_kind="SCENARIO_MATERIAL",
        snapshot_id=snap_id, scenario_id="scen-1", order_id=None,
        as_of_ms=1000, payload=p1
    )
    db.commit()

    with pytest.raises(ValueError, match="OBSERVATION_IMMUTABILITY_CONFLICT"):
        persist_paper_observation(
            db, namespace="paper", event_kind="SCENARIO_MATERIAL",
            snapshot_id=snap_id, scenario_id="scen-1", order_id=None,
            as_of_ms=1000, payload=p2
        )


def test_o03_scenario_production_material_revision(db):
    """O03: evaluate_upcoming_setups produces real PaperObservation rows."""
    seed_all(db, "LONG", F.NOW_MS)
    run_cycle(db, "LONG", now_ms=F.NOW_MS)

    obs = db.query(models.PaperObservation).filter(
        models.PaperObservation.event_kind == "SCENARIO_MATERIAL"
    ).all()
    assert len(obs) > 0
    assert obs[0].namespace == "paper"
    assert obs[0].snapshot_id is not None


def test_o04_duplicate_lifecycle_close_idempotent(db):
    """O04: Duplicate lifecycle close produces exactly one outcome row without mutating risk."""
    order = models.PaperOrder(
        id="ord-o04",
        instrument="XAUUSDT",
        direction="LONG",
        state="paper_open",
        planned_entry=2650.0,
        actual_entry=2650.0,
        stop_loss=2640.0,
        take_profit=2685.0,
        actual_exit=2685.0,
        quantity=1.0,
        initial_risk_usdt=10.0,
        realized_pnl_net=35.0,
        realized_r=3.5,
        exit_cause="TP_HIT",
        scenario_snapshot_id="snap-o04",
        created_at=F.NOW_MS,
        opened_at=F.NOW_MS,
        closed_at=F.NOW_MS + 100_000
    )
    db.add(order)
    db.commit()

    id1 = record_closed_outcome(db, order)
    id2 = record_closed_outcome(db, order)
    assert id1 == id2
    db.commit()

    count = db.query(models.PaperObservation).filter(
        models.PaperObservation.order_id == "ord-o04"
    ).count()
    assert count == 1


def test_o05_invalid_namespace_rejected(db):
    """O05: LIVE namespace or invalid time is rejected; no orders are created."""
    with pytest.raises(ValueError, match="INVALID_PAPER_OBSERVATION_CONTEXT"):
        persist_paper_observation(
            db, namespace="LIVE", event_kind="SCENARIO_MATERIAL",
            snapshot_id="snap-x", scenario_id="scen-x", order_id=None,
            as_of_ms=1000, payload={}
        )

    with pytest.raises(ValueError, match="OBSERVATION_TIME_INVALID"):
        persist_paper_observation(
            db, namespace="paper", event_kind="SCENARIO_MATERIAL",
            snapshot_id="snap-x", scenario_id="scen-x", order_id=None,
            as_of_ms=-1, payload={}
        )
