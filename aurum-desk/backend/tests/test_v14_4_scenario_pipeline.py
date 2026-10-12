"""
V14.4.1 strict LONG / SHORT reversal pipeline on the REAL engine (detector -> scenario -> chain -> plan).

No hand-built evidence nodes: the chain is produced from fixture candles by evaluate_scenarios. The full
persist -> watch -> guard -> arm -> fill -> quota path lives in test_v14_4_1_production_pipeline.py.
"""
import pytest

from services.smc_scenario_config import active_scenario_config as CFG
from services.smc_scenario_service import evaluate_scenarios, load_closed_context
from services.smc_scenario_types import ScenarioState

import smc_fixtures as F


def run(direction, mutate=None, now_ms=F.NOW_MS):
    cbt = F.make_candles_by_tf(direction, now_ms)
    if mutate:
        mutate(cbt)
    ctx = load_closed_context(cbt, now_ms, CFG)
    snaps = evaluate_scenarios("XAUUSDT", ctx, [], CFG, now_ms, settings=F.default_settings())
    return {s.direction: s for s in snaps if s.direction}, snaps


@pytest.mark.parametrize("direction,swept_kind,bias", [("LONG", "SWEEP_LOW", "BULLISH"), ("SHORT", "SWEEP_HIGH", "BEARISH")])
def test_reversal_confirmed_pipeline(direction, swept_kind, bias):
    snaps, _ = run(direction)
    s = snaps[direction]
    assert s.state == ScenarioState.STRUCTURE_CONFIRMED
    assert s.trigger_chain["verified"] is True and s.blockers == []
    by_kind = {e["kind"]: e for e in s.evidence if e["kind"] in (swept_kind, "MSS", "FVG", "RETEST")}
    assert set(by_kind) == {swept_kind, "MSS", "FVG", "RETEST"}
    sweep, brk, fvg, retest = (by_kind[k] for k in (swept_kind, "MSS", "FVG", "RETEST"))
    # causal order and real links (predicate-derived, not self-declared)
    assert sweep["occurred_at_ms"] < brk["occurred_at_ms"] < retest["occurred_at_ms"]
    assert brk["known_at_ms"] <= fvg["known_at_ms"] <= retest["occurred_at_ms"]
    assert sweep["id"] in brk["source_ids"] and brk["id"] in fvg["source_ids"] and fvg["id"] in retest["source_ids"]
    # displacement was MEASURED on the prefix ATR
    ex = brk["extra"]
    assert ex["displacement"]["is_qualified"] is True and ex["displacement"]["ratio"] >= 0.8
    assert ex["prefix_atr"] > 0 and ex["displacement"]["close_beyond"] is True
    # sweep stores the real wick extreme, distinct from the swept level
    assert sweep["extra"]["wick_extreme"] != sweep["level"]
    plan = s.plan
    assert plan["is_valid"] is True
    if direction == "LONG":
        assert plan["stop_loss"] < plan["planned_entry"] < plan["take_profit"]
        assert plan["stop_loss"] < sweep["extra"]["wick_extreme"]        # SL beyond the ACTUAL wick extreme
    else:
        assert plan["take_profit"] < plan["planned_entry"] < plan["stop_loss"]
        assert plan["stop_loss"] > sweep["extra"]["wick_extreme"]
    assert plan["net_rr"] >= CFG.min_net_rr
    assert plan["net_reward_usdt"] > 0 and plan["net_risk_usdt"] > 0
    assert plan["can_execute"] is True
    # target is real, unconsumed liquidity on the correct side (not entry +/- 20)
    assert s.target is not None and s.target["price"] == plan["take_profit"]
    assert abs(plan["take_profit"] - plan["planned_entry"]) != 20.0


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_opposite_direction_stays_observation(direction):
    snaps, all_ = run(direction)
    other = "SHORT" if direction == "LONG" else "LONG"
    assert snaps[other].state != ScenarioState.STRUCTURE_CONFIRMED
    assert snaps[other].plan is None
    assert len(all_) == 2


def test_short_is_exact_price_mirror_of_long():
    long_s, _ = run("LONG")
    short_s, _ = run("SHORT")
    lp, sp = long_s["LONG"].plan, short_s["SHORT"].plan
    k = F.MIRROR_K
    assert round(k - lp["planned_entry"], 2) == sp["planned_entry"]
    assert round(k - lp["stop_loss"], 2) == sp["stop_loss"]
    assert round(k - lp["take_profit"], 2) == sp["take_profit"]
    assert abs(lp["net_rr"] - sp["net_rr"]) < 0.01
