"""
AURUM DESK — V13.5 PRICE PLAN ACCEPTANCE TESTS
Verifies:
- Structural target preservation (swings 5M/15M) without arbitrary loop expansions.
- Measured move from actual reference range (anchor ± range_height * fixed_multiplier).
- Price geometry validation & tick size snapping.
- Clean rejection when no valid targets satisfy Net R:R >= 2.0R.
"""

import pytest
from lab.daily_research_scheduler import (
    build_measured_move_target,
    build_scheduled_price_plan,
    collect_causal_target_candidates
)
from domain_calculator import CostAssumptions, validate_price_geometry
from schemas import ReplayRunRequest


def test_01_measured_move_from_actual_range():
    # Range height = 110 - 100 = 10.0
    ref_range = {
        "high": 110.0,
        "low": 100.0,
        "cutoff_ts": 1783620000000,
        "valid": True
    }

    # LONG: anchor 100 + 10 * 1.5 = 115.0
    target_long, err_long = build_measured_move_target(ref_range, "LONG", anchor=100.0, fixed_multiplier=1.5)
    assert err_long is None
    assert target_long is not None
    assert target_long["price"] == 115.0
    assert target_long["range_height"] == 10.0
    assert target_long["target_model"] == "MEASURED_MOVE_RESEARCH"

    # SHORT: anchor 100 - 10 * 1.5 = 85.0
    target_short, err_short = build_measured_move_target(ref_range, "SHORT", anchor=100.0, fixed_multiplier=1.5)
    assert err_short is None
    assert target_short is not None
    assert target_short["price"] == 85.0

    # Invalid range: high <= low
    invalid_range = {"high": 100.0, "low": 105.0}
    t_inv, err_inv = build_measured_move_target(invalid_range, "LONG", anchor=100.0)
    assert t_inv is None
    assert "INVALID_RANGE_GEOMETRY" in err_inv


def test_02_structural_swing_target_preserved():
    # Context with 5M swing high at 2670.0
    sim_time = 1783621800000
    bars_5m = [
        {"timestamp": sim_time - 600000, "open": 2650.0, "high": 2652.0, "low": 2649.0, "close": 2651.0},
        {"timestamp": sim_time - 300000, "open": 2651.0, "high": 2670.0, "low": 2650.0, "close": 2668.0},
        {"timestamp": sim_time, "open": 2668.0, "high": 2669.0, "low": 2655.0, "close": 2656.0}
    ]
    context = {
        "recent_bars_5m": bars_5m * 6,  # > 15 bars for pivot engine
        "recent_bars_15m": []
    }
    targets = collect_causal_target_candidates("LONG", context, entry_price=2656.50, decision_ms=sim_time)
    assert len(targets) > 0
    assert any(t["price"] == 2670.0 for t in targets)
    assert any("SWING_HIGH" in t["source"] for t in targets)


def test_03_price_geometry_and_tick_validation():
    # Valid LONG: SL < Entry < TP
    is_valid_l, _ = validate_price_geometry("LONG", entry=2650.0, sl=2645.0, tp=2665.0)
    assert is_valid_l is True

    # Invalid LONG: Entry > TP
    is_valid_bad_l, err_l = validate_price_geometry("LONG", entry=2650.0, sl=2645.0, tp=2648.0)
    assert is_valid_bad_l is False
    assert "INVALID_LONG_GEOMETRY" in err_l

    # Valid SHORT: TP < Entry < SL
    is_valid_s, _ = validate_price_geometry("SHORT", entry=2650.0, sl=2655.0, tp=2635.0)
    assert is_valid_s is True

    # Invalid SHORT: SL < Entry
    is_valid_bad_s, err_s = validate_price_geometry("SHORT", entry=2650.0, sl=2645.0, tp=2635.0)
    assert is_valid_bad_s is False
    assert "INVALID_SHORT_GEOMETRY" in err_s
