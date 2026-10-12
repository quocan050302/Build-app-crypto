"""
V14.4.1 fail-closed behaviour on the real engine: no fake FVG / target / wick, open-bar retest, bad quote, missing data.
"""
import dataclasses

import pytest

from services.smc_chain_service import confirm_closed_retest, confirm_quote_retest
from services.smc_liquidity_service import select_unconsumed_target, update_liquidity_state
from services.smc_scenario_config import active_scenario_config as CFG
from services.smc_scenario_plan import build_scenario_plan
from services.smc_scenario_service import evaluate_scenarios, load_closed_context
from services.smc_scenario_types import LiquidityLevel, ScenarioState

import smc_fixtures as F
from test_v14_4_scenario_pipeline import run


def _tail(cbt, k):
    return cbt["5M"][-k]


def _regen(cbt, direction, now_ms=F.NOW_MS):
    m5 = cbt["5M"]
    cbt.update({"15M": F.aggregate(m5, 15, now_ms), "1H": F.aggregate(m5, 60, now_ms), "4H": F.aggregate(m5, 240, now_ms)})


def test_no_fake_fvg_when_no_gap_exists():
    def no_gap(cbt):
        _tail(cbt, 5).high = 2648.6          # t14 high above t16 low 2648.2 -> the 3-candle gap disappears
        _regen(cbt, "LONG")
    snaps, _ = run("LONG", no_gap)
    s = snaps["LONG"]
    assert s.state != ScenarioState.STRUCTURE_CONFIRMED
    assert s.trigger_chain["verified"] is False
    assert s.plan is None
    fvgs = [e for e in s.evidence if e["kind"] == "FVG"]
    assert all(e["extra"]["top"] - e["extra"]["bottom"] != 1.0 for e in fvgs)   # never level +/- 0.5


def test_open_bar_retest_is_not_confirmed():
    def open_last(cbt):
        _tail(cbt, 1).is_closed = False      # the retest candle is still forming
    snaps, _ = run("LONG", open_last)
    assert snaps["LONG"].state != ScenarioState.STRUCTURE_CONFIRMED
    assert snaps["LONG"].plan is None
    zone = {"top": 2648.2, "bottom": 2647.0}
    forming = dataclasses.replace(_tail(F.make_candles_by_tf("LONG"), 1), is_closed=False)
    assert confirm_closed_retest(zone, forming, "LONG") == (False, None)


def test_no_target_means_no_plan_and_no_plus_minus_20():
    plan = build_scenario_plan(symbol="XAUUSDT", direction="LONG", entry_price=2648.6, sweep_extreme=2644.8,
                               target_level=None, config=CFG, settings=F.default_settings())
    assert plan.is_valid is False and plan.invalid_reason == "NO_VALID_TARGET"
    assert plan.take_profit == 0.0 and plan.take_profit != 2648.6 + 20


def test_missing_wick_extreme_blocks_certified_plan():
    for d, entry, tgt in (("LONG", 2648.6, 2666.0), ("SHORT", 2651.4, 2634.0)):
        plan = build_scenario_plan(symbol="XAUUSDT", direction=d, entry_price=entry, sweep_extreme=None,
                                   target_level=tgt, config=CFG, settings=F.default_settings())
        assert plan.is_valid is False and plan.invalid_reason == "SWEEP_EXTREME_MISSING"


def test_wrong_side_target_is_rejected_by_geometry():
    plan = build_scenario_plan(symbol="XAUUSDT", direction="LONG", entry_price=2648.6, sweep_extreme=2644.8,
                               target_level=2640.0, config=CFG, settings=F.default_settings())
    assert plan.is_valid is False and plan.invalid_reason == "INVALID_PRICE_GEOMETRY"


def test_consumed_target_is_not_reselected():
    lv = LiquidityLevel(id="L1", price=2666.0, side="BUY_SIDE", source_kind="SWING", label_vi="Đỉnh", state="UNTOUCHED",
                        occurred_at_ms=1_000, known_at_ms=2_000)
    assert select_unconsumed_target([lv], "LONG", 2650.0, as_of_ms=10_000_000) is lv
    dur = 300_000
    wick = F.C(timestamp=3_000_000, open=2660.0, high=2667.0, low=2659.0, close=2661.0)   # takes the stop, closes back
    update_liquidity_state(lv, [wick], 10_000_000, dur)
    assert lv.state == "SWEPT"
    assert select_unconsumed_target([lv], "LONG", 2650.0, as_of_ms=10_000_000) is None
    broke = F.C(timestamp=4_000_000, open=2661.0, high=2670.0, low=2660.0, close=2669.0)
    update_liquidity_state(lv, [wick, broke], 10_000_000, dur)
    assert lv.state == "BROKEN"
    # a level learned AFTER the decision time is not a target either
    fresh = dataclasses.replace(lv, state="UNTOUCHED", known_at_ms=20_000_000)
    assert select_unconsumed_target([fresh], "LONG", 2650.0, as_of_ms=10_000_000) is None


def test_short_target_side_filter():
    lv = LiquidityLevel(id="L2", price=2630.0, side="SELL_SIDE", source_kind="SWING", label_vi="Đáy", state="UNTOUCHED",
                        occurred_at_ms=1_000, known_at_ms=2_000)
    up = dataclasses.replace(lv, id="L3", price=2670.0, side="BUY_SIDE")
    assert select_unconsumed_target([lv, up], "SHORT", 2650.0) is lv
    assert select_unconsumed_target([up], "SHORT", 2650.0) is None


def test_quote_retest_requires_valid_fresh_quote_after_fvg():
    zone = {"top": 2648.2, "bottom": 2647.0}

    class Q:
        def __init__(self, **k):
            self.is_valid, self.is_stale, self.ask, self.bid, self.exchange_time, self.observed_at = True, False, 2648.0, 2647.8, 2_000, None
            self.__dict__.update(k)
    assert confirm_quote_retest(zone, Q(), "LONG", 1_000) == (True, None)
    assert confirm_quote_retest(zone, Q(is_valid=False), "LONG", 1_000) == (False, "QUOTE_NOT_VALID")
    assert confirm_quote_retest(zone, Q(is_stale=True), "LONG", 1_000)[0] is False
    assert confirm_quote_retest(zone, Q(exchange_time=None), "LONG", 1_000)[0] is False
    assert confirm_quote_retest(zone, Q(exchange_time=500), "LONG", 1_000)[0] is False   # before FVG known


def test_too_few_bars_is_data_unavailable_not_failed_smc():
    cbt = F.make_candles_by_tf("LONG")
    cbt["5M"] = cbt["5M"][-10:]
    ctx = load_closed_context(cbt, F.NOW_MS, CFG)
    out = evaluate_scenarios("XAUUSDT", ctx, [], CFG, F.NOW_MS, settings=F.default_settings())
    assert len(out) == 1 and out[0].plan is None
    assert any(b.startswith("DATA_UNAVAILABLE") for b in out[0].blockers)
    assert out[0].state != ScenarioState.STRUCTURE_CONFIRMED


def test_atr_period_is_a_validated_config_field():
    assert CFG.displacement_atr_period == 14
    with pytest.raises(Exception):
        dataclasses.replace(CFG, displacement_atr_period=0)


def test_malformed_candle_is_reported_not_silently_filtered():
    cbt = F.make_candles_by_tf("LONG")
    cbt["5M"][50].high = float("nan")
    ctx = load_closed_context(cbt, F.NOW_MS, CFG)
    assert ctx.blocking_issues, "NaN OHLC in a required timeframe must be a typed blocking issue"
    out = evaluate_scenarios("XAUUSDT", ctx, [], CFG, F.NOW_MS, settings=F.default_settings())
    assert out[0].state != ScenarioState.STRUCTURE_CONFIRMED and out[0].plan is None
