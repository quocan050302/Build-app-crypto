"""
Aurum Desk V13.4 — Unit tests for structural price plans, targets, and geometry validation.
Phần 28, 29, 30, 31, 32, 79.
"""
import pytest
from domain_calculator import CostAssumptions, validate_price_geometry
from lab.daily_research_scheduler import (
    collect_causal_target_candidates,
    build_measured_move_target,
    build_scheduled_price_plan
)
from schemas import ReplayRunRequest


def test_01_structural_target_preservation():
    """P01: If a 5M swing high exists and satisfies Net RR >= 2.0, preserve exact swing price and model."""
    decision_ms = 1785000000000
    context = {
        "recent_bars_5m": [
            # Create candles with a clear swing high around 2670
            {"timestamp": decision_ms - (20 - i) * 300000, "open": 2650.0, "high": 2652.0 if i != 10 else 2670.0, "low": 2648.0, "close": 2650.0, "volume": 100.0, "timeframe": "5m", "is_closed": True}
            for i in range(20)
        ],
        "recent_bars_15m": []
    }
    targets = collect_causal_target_candidates("LONG", context, entry_price=2650.0, decision_ms=decision_ms)
    assert len(targets) > 0, "Expected at least 1 structural target candidate"
    assert any(t["price"] == 2670.0 for t in targets), "Expected swing high at 2670.0 to be captured"
    sh_target = next(t for t in targets if t["price"] == 2670.0)
    assert sh_target["target_model"] == "STRUCTURAL_5M_SWING_HIGH"


def test_02_no_artificial_loop_multiplier_expansion():
    """P02: build_scheduled_price_plan does NOT loop over multipliers [2.2, 2.5, 3.0...] to force a fake target."""
    decision_ms = 1785000000000
    curr_quote = {
        "sim_time": decision_ms,
        "close": 2650.0,
        "spread_usd": 0.20,
        "costs": CostAssumptions(taker_fee_pct=0.0006, maker_fee_pct=0.0002, slippage_usd=0.10, spread_usd=0.20),
        "capital": 1000.0
    }
    config = ReplayRunRequest(leverage=30, quota_risk_pct=0.10)
    # Context with empty bars -> should fallback cleanly to measured move or reject
    context = {"recent_bars_5m": [], "recent_bars_15m": []}

    plan, err = build_scheduled_price_plan("LONG", context, curr_quote, config)
    if plan is not None:
        assert plan["calc"].net_rr >= 2.0
        # Target model must reflect reality: MEASURED_RANGE_EXTENSION_RESEARCH, not STRUCTURAL swing!
        assert plan["target_model"] == "MEASURED_RANGE_EXTENSION_RESEARCH"
    else:
        assert "NO_VALID_STRUCTURAL_TARGET" in err


def test_03_geometry_validation_and_tick_sanity():
    """P03: Geometry requires SL < entry < TP for LONG, and TP < entry < SL for SHORT."""
    v_long, _ = validate_price_geometry("LONG", entry=2650.0, sl=2645.0, tp=2665.0)
    assert v_long is True

    inv_long, err = validate_price_geometry("LONG", entry=2650.0, sl=2655.0, tp=2665.0)
    assert inv_long is False
    assert "SL" in err

    v_short, _ = validate_price_geometry("SHORT", entry=2650.0, sl=2655.0, tp=2635.0)
    assert v_short is True

    inv_short, err_s = validate_price_geometry("SHORT", entry=2650.0, sl=2645.0, tp=2635.0)
    assert inv_short is False
