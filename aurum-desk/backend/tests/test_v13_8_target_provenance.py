import pytest
from lab.daily_research_scheduler import build_measured_move_target, collect_causal_target_candidates, build_scheduled_price_plan
from domain_calculator import validate_price_geometry, CostAssumptions

def test_measured_move_from_range_height():
    # PHẦN 32, 52, 101: range 4110–4120 (height=10), anchor=4123, multiplier=2
    ref_range = {
        "high": 4120.0,
        "low": 4110.0,
        "cutoff_ts": 1000,
        "valid": True
    }
    target_long, err_l = build_measured_move_target(ref_range, "LONG", anchor=4123.0, fixed_multiplier=2.0)
    assert err_l is None
    # LONG: 4123 + (10 * 2) = 4143
    assert target_long["price"] == 4143.0
    assert target_long["source"] == "PRE_NY_MEASURED_MOVE"
    assert target_long["target_model"] == "MEASURED_MOVE_RESEARCH"

    target_short, err_s = build_measured_move_target(ref_range, "SHORT", anchor=4123.0, fixed_multiplier=2.0)
    assert err_s is None
    # SHORT: 4123 - (10 * 2) = 4103
    assert target_short["price"] == 4103.0
    assert target_short["source"] == "PRE_NY_MEASURED_MOVE"

def test_invalid_range_rejected():
    # Range high <= low rejected
    bad_range = {"high": 4110.0, "low": 4120.0, "cutoff_ts": 1000}
    t, err = build_measured_move_target(bad_range, "LONG", anchor=4123.0)
    assert t is None
    assert "INVALID_RANGE_GEOMETRY" in err

def test_geometry_rejects_wrong_side_target():
    # PHẦN 30, 101: SHORT with TP above entry is invalid geometry
    # SHORT entry 4123, SL 4133, TP 4151 (above entry!)
    is_valid, err = validate_price_geometry("SHORT", entry=4123.0, sl=4133.0, tp=4151.0)
    assert is_valid is False
    assert "TP" in err or "geometry" in err.lower()

def test_no_multiplier_search_loop_rejects_when_rr_unmet():
    # PHẦN 07, 51, 134: build_scheduled_price_plan uses single frozen multiplier
    # If Net RR is not met with this frozen multiplier, it does NOT loop to find a bigger multiplier!
    context = {
        "pre_ny_range": {
            "high": 4120.0,
            "low": 4119.0, # Tiny range height = 1.0
            "cutoff_ts": 1000,
            "valid": True
        }
    }
    curr_quote = {
        "sim_time": 2000,
        "close": 4121.0,
        "spread_usd": 0.35,
        "costs": CostAssumptions(),
        "capital": 1000.0
    }
    class MockConfig:
        leverage = 30
        margin_mode = "ISOLATED"
        quota_risk_pct = 0.10
        measured_move_multiplier = 1.0 # Frozen 1.0 multiplier -> target cannot reach 2.0R

    plan, reason = build_scheduled_price_plan("LONG", context, curr_quote, MockConfig())
    assert plan is None
    assert reason is not None
