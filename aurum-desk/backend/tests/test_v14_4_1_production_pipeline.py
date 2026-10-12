"""
V14.4.1 production pipeline tests.

REAL chain (no helper-only shortcuts): persisted candles -> StrategyService.evaluate_upcoming_setups ->
market-reading cycle (detector -> scenario -> persist/commit/event) -> promotion to WatchSetup ->
exact-binding certified guard -> guarded auto-arm -> ExecutionCoordinator fill -> lifecycle/quota.
Only data (candles), the injected clock and the quote are fixtures.
"""
import json

import pytest

import models
from services.clock import FakeClock
from services.execution_coordinator import execution_coordinator
from services.lesson_policy_service import LessonPolicyService
from services.lesson_rule_service import LessonRuleService
from services.strategy_service import strategy_service
from services.trading_policy_service import TradingPolicyService

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


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_full_cycle_scenario_to_armed_order_with_exact_refs(db, direction):
    seed_all(db, direction)
    run_cycle(db, direction)

    assert strategy_service.last_market_reading["status"] == "OK", strategy_service.last_market_reading
    snaps = db.query(models.SMCScenarioSnapshot).filter_by(direction=direction, state="STRUCTURE_CONFIRMED").all()
    assert len(snaps) == 1
    payload = json.loads(snaps[0].payload)
    assert payload["trigger_chain"]["verified"] is True
    assert payload["plan"]["is_valid"] is True

    ws = db.query(models.WatchSetup).filter_by(id="watch-XAUUSDT-5M").one()
    assert ws.scenario_snapshot_id == snaps[0].snapshot_id
    assert ws.setup_instance_id == snaps[0].instance_id
    assert ws.strategy_family == "STANDARD_SMC" and ws.direction == direction
    assert ws.state == "ARMED", str(strategy_service.last_market_reading.get("armed"))

    orders = armed_orders(db)
    assert len(orders) == 1
    o = orders[0]
    # exact frozen refs, each into ITS OWN table
    assert o.scenario_snapshot_id == snaps[0].snapshot_id
    assert o.quality_snapshot_id is not None
    assert db.query(models.SetupQualitySnapshot).filter_by(snapshot_id=o.quality_snapshot_id).count() == 1
    assert o.evidence_snapshot_id is None
    # signed geometry
    if direction == "LONG":
        assert o.stop_loss < o.planned_entry < o.take_profit
    else:
        assert o.take_profit < o.planned_entry < o.stop_loss
    assert o.planned_entry == payload["plan"]["planned_entry"]
    assert o.stop_loss == payload["plan"]["stop_loss"] and o.take_profit == payload["plan"]["take_profit"]
    # ARMED does not count toward quota (only an actual PAPER fill does)
    audit = db.query(models.DayAudit).first()
    assert (audit.fills_count if audit else 0) == 0
    # events are published only for persisted revisions
    assert db.query(models.DomainEvent).filter_by(event_type="scenario.updated").count() >= 1


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_full_cycle_fill_counts_quota_and_second_cycle_is_idempotent(db, direction):
    seed_all(db, direction)
    run_cycle(db, direction)
    assert len(armed_orders(db)) == 1
    run_cycle(db, direction)                      # same tape again: no second order, no duplicate scenario revision
    assert db.query(models.PaperOrder).count() == 1
    snaps_before = db.query(models.SMCScenarioSnapshot).count()
    run_cycle(db, direction)
    assert db.query(models.SMCScenarioSnapshot).count() == snaps_before

    order = db.query(models.PaperOrder).one()
    fill_tk = {
        "symbol": "XAUUSDT",
        "bid": round(order.planned_entry - 0.2, 2) if direction == "LONG" else order.planned_entry,
        "ask": order.planned_entry if direction == "LONG" else round(order.planned_entry + 0.2, 2),
        "last": order.planned_entry,
        "timestamp": F.NOW_MS - 200,
        "observed_at": F.NOW_MS - 200
    }
    execution_coordinator.evaluate_orders_sync(
        db, ticker_override=fill_tk, clock=FakeClock(F.NOW_MS), now_ms=F.NOW_MS)
    db.refresh(order)
    assert order.state == "paper_open", order.state
    audit = db.query(models.DayAudit).first()
    assert audit.fills_count == 1
    assert order.scenario_snapshot_id is not None   # frozen ref survives the fill


def test_auto_off_promotes_ready_watch_but_arms_nothing(db):
    seed_all(db, "LONG")
    run_cycle(db, "LONG", auto=False)
    ws = db.query(models.WatchSetup).filter_by(id="watch-XAUUSDT-5M").one()
    assert ws.state == "READY" and ws.scenario_snapshot_id
    assert armed_orders(db) == []


def test_red_news_blocks_arm_but_keeps_scenario_facts(db):
    seed_all(db, "LONG")
    make_news(db, F.NOW_MS, offset_min=10)
    run_cycle(db, "LONG")
    assert armed_orders(db) == []
    assert db.query(models.SMCScenarioSnapshot).filter_by(state="STRUCTURE_CONFIRMED").count() == 1
    ws = db.query(models.WatchSetup).filter_by(id="watch-XAUUSDT-5M").one()
    assert ws.state == "READY"


def test_stale_quote_and_feed_gap_block_arm(db):
    seed_all(db, "LONG")
    run_cycle(db, "LONG", tk=tick("LONG", F.NOW_MS, age_ms=120_000))
    assert armed_orders(db) == []
    run_cycle(db, "LONG", feed={"has_execution_gap": True, "is_lagging": False})
    assert armed_orders(db) == []


def test_scenario_exception_never_blocks_the_rest_of_the_cycle(db, monkeypatch):
    seed_all(db, "LONG")
    import services.smc_scenario_service as svc

    def boom(*a, **k):
        raise RuntimeError("injected scenario failure")
    monkeypatch.setattr(svc, "evaluate_scenario_cycle", boom)
    run_cycle(db, "LONG")                         # must not raise; legacy + NY cycles still ran
    assert strategy_service.last_market_reading["status"] == "SYSTEM_ERROR"
    assert armed_orders(db) == []


def test_flag_off_disables_market_reading_and_never_certifies(db, monkeypatch):
    import dataclasses
    from services import smc_scenario_config as cfg_mod
    seed_all(db, "LONG")
    monkeypatch.setattr(cfg_mod, "active_scenario_config",
                        dataclasses.replace(cfg_mod.active_scenario_config, scenario_explain_enabled=False))
    run_cycle(db, "LONG")
    assert strategy_service.last_market_reading["status"] == "DISABLED_BY_FLAG"
    assert db.query(models.SMCScenarioSnapshot).count() == 0
    assert armed_orders(db) == []
