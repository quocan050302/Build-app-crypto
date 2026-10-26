"""
backend/tests/test_v16_39_21_e2e_financial_fixtures.py
Aurum V16.39.21 — Preflight & Canonical Verification for E2E Fixtures

Covers Checklist 011-020, 111-120:
1. Canonical calculation via domain_calculator.calculate_risk_reward (no fake gross-0.05).
2. Preflight LONG (2650/2640/2674) with actual Net R:R ~1.7677 in (1.50, 2.50).
3. Preflight SHORT (2650/2660/2626) with actual Net R:R ~1.7681 in (1.50, 2.50).
4. Metamorphic test: changing min_net_rr preserves geometry, quantity, and net_rr.
5. Invalidation test: rejects invalid geometry (e.g. SL on wrong side).
6. Regression test: baseline LONG 2668 is ~1.2807, proving it failed 1.50 threshold.
"""
import pytest
from domain_calculator import CostAssumptions, calculate_risk_reward
from tests.e2e_financial_helper import _calculate_e2e_plan_financials


def test_calculate_e2e_plan_financials_matches_domain_calculator_directly():
    """Verify helper is a pure pass-through to authoritative domain_calculator."""
    helper_res = _calculate_e2e_plan_financials(
        direction="LONG",
        entry=2650.0,
        sl=2640.0,
        tp=2674.0,
        capital=1000.0,
        risk_pct=0.25,
        min_net_rr=1.5,
        leverage=5,
        margin_mode="ISOLATED",
    )
    direct_res = calculate_risk_reward(
        direction="LONG",
        entry=2650.0,
        sl=2640.0,
        tp=2674.0,
        capital=1000.0,
        risk_pct=0.25,
        min_net_rr=1.5,
        costs=CostAssumptions(),
        entry_has_slippage=False,
        leverage=5,
        margin_mode="ISOLATED",
    )

    assert helper_res.is_valid is True
    assert direct_res.is_valid is True
    assert helper_res.gross_rr == direct_res.gross_rr
    assert helper_res.net_rr == direct_res.net_rr
    assert helper_res.quantity == direct_res.quantity
    assert helper_res.meets_min_rr == direct_res.meets_min_rr


def test_preflight_mid_long_fixture_between_thresholds():
    """Verify MID_LONG (2650/2640/2674) Net R:R is ~1.7677, MET at 1.50 and NOT_MET at 2.50."""
    res_15 = _calculate_e2e_plan_financials("LONG", 2650.0, 2640.0, 2674.0, min_net_rr=1.5)
    res_25 = _calculate_e2e_plan_financials("LONG", 2650.0, 2640.0, 2674.0, min_net_rr=2.5)

    assert res_15.is_valid is True
    assert res_25.is_valid is True

    # Geometry verification
    assert res_15.stop_loss < res_15.planned_entry < res_15.take_profit
    assert res_15.planned_entry == 2650.0
    assert res_15.stop_loss == 2640.0
    assert res_15.take_profit == 2674.0

    # Net RR is strictly between 1.50 and 2.50
    assert 1.50 < res_15.net_rr < 2.50
    assert pytest.approx(res_15.net_rr, abs=1e-3) == 1.768

    # Threshold gating
    assert res_15.meets_min_rr is True
    assert res_25.meets_min_rr is False

    # Metamorphic invariance across threshold changes
    assert res_15.net_rr == res_25.net_rr
    assert res_15.quantity == res_25.quantity
    assert res_15.planned_entry == res_25.planned_entry
    assert res_15.stop_loss == res_25.stop_loss
    assert res_15.take_profit == res_25.take_profit


def test_preflight_mid_short_fixture_between_thresholds():
    """Verify MID_SHORT (2650/2660/2626) Net R:R is ~1.7681, MET at 1.50 and NOT_MET at 2.50."""
    res_15 = _calculate_e2e_plan_financials("SHORT", 2650.0, 2660.0, 2626.0, min_net_rr=1.5)
    res_25 = _calculate_e2e_plan_financials("SHORT", 2650.0, 2660.0, 2626.0, min_net_rr=2.5)

    assert res_15.is_valid is True
    assert res_25.is_valid is True

    # Geometry verification: for SHORT, tp < entry < sl
    assert res_15.take_profit < res_15.planned_entry < res_15.stop_loss
    assert res_15.planned_entry == 2650.0
    assert res_15.stop_loss == 2660.0
    assert res_15.take_profit == 2626.0

    # Net RR is strictly between 1.50 and 2.50
    assert 1.50 < res_15.net_rr < 2.50
    assert pytest.approx(res_15.net_rr, abs=1e-3) == 1.768

    # Threshold gating
    assert res_15.meets_min_rr is True
    assert res_25.meets_min_rr is False

    # Metamorphic invariance
    assert res_15.net_rr == res_25.net_rr
    assert res_15.quantity == res_25.quantity


def test_regression_old_fixture_below_150():
    """Prove baseline LONG 2668 had Net RR ~1.2807, failing minimum 1.50."""
    res_old = calculate_risk_reward(
        direction="LONG",
        entry=2650.0,
        sl=2640.0,
        tp=2668.0,
        capital=1000.0,
        risk_pct=0.25,
        min_net_rr=1.5,
        costs=CostAssumptions(),
        entry_has_slippage=False,
        leverage=5,
        margin_mode="ISOLATED",
    )
    assert res_old.is_valid is True
    assert pytest.approx(res_old.net_rr, abs=1e-3) == 1.281
    assert res_old.meets_min_rr is False


def test_invalid_geometry_rejected():
    """Verify invalid geometry raises ValueError in fixture helper."""
    with pytest.raises(ValueError, match="E2E plan invalid"):
        # Inverted LONG: SL > Entry
        _calculate_e2e_plan_financials("LONG", 2650.0, 2660.0, 2674.0)

    with pytest.raises(ValueError, match="E2E plan invalid"):
        # Inverted SHORT: TP > Entry
        _calculate_e2e_plan_financials("SHORT", 2650.0, 2660.0, 2680.0)
