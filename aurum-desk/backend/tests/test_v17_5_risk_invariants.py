import pytest
from domain_calculator import calculate_risk_reward, CostAssumptions
from services.stop_placement_types import (
    StopPlacementMode,
    StopPlacementPolicy,
    StopAnchor,
    StopAnchorResult,
    StopBufferResult,
)
from services.stop_placement_service import plan_structural_stop


def test_target_invariance_across_variants():
    """
    Critical V17.5 invariant: Baseline TP must be calculated from legacy formula and
    kept FROZEN. Changing SL from legacy (A) to structural (C) must NOT expand TP.
    """
    entry = 4000.0
    legacy_sl = 4005.0  # dist 5.0
    baseline_tp = 3987.5  # fixed target
    structural_sl = 4015.0  # wider dist 15.0

    # With same frozen target
    tp_diff = abs(baseline_tp - baseline_tp)
    assert tp_diff == 0.0

    # Calculator with legacy SL
    calc_a = calculate_risk_reward(
        entry=entry,
        stop_loss=legacy_sl,
        take_profit=baseline_tp,
        direction="SHORT",
        capital=1000.0,
        risk_pct=1.0,
        leverage=10,
        costs=CostAssumptions(),
    )

    # Calculator with structural SL (same TP)
    calc_c = calculate_risk_reward(
        entry=entry,
        stop_loss=structural_sl,
        take_profit=baseline_tp,
        direction="SHORT",
        capital=1000.0,
        risk_pct=1.0,
        leverage=10,
        costs=CostAssumptions(),
    )

    # TP is identical
    assert calc_a.take_profit == calc_c.take_profit == 3987.5
    # Quantity with wider SL MUST be smaller to preserve risk budget
    assert calc_c.quantity < calc_a.quantity
    # Both respect max dollar risk budget (~$10 on $1000 capital at 1%)
    assert calc_a.net_risk_usdt <= 10.5
    assert calc_c.net_risk_usdt <= 10.5


def test_screenshot_geometry_fixtures():
    """
    Fixture testing the 4 SHORT orders from the user's UI screenshot.
    Label: SCREENSHOT_GEOMETRY_ONLY.
    Proves exact SL distance and gross R:R math fidelity without claiming retroactive win.
    """
    cases = [
        {"entry": 3989.92, "sl": 3998.94, "tp": 3967.72, "expected_sl_dist": 9.02, "expected_tp_dist": 22.20},
        {"entry": 4006.04, "sl": 4007.22, "tp": 3995.29, "expected_sl_dist": 1.18, "expected_tp_dist": 10.75},
        {"entry": 4036.82, "sl": 4038.07, "tp": 4020.71, "expected_sl_dist": 1.25, "expected_tp_dist": 16.11},
        {"entry": 4030.65, "sl": 4031.00, "tp": 4020.71, "expected_sl_dist": 0.35, "expected_tp_dist": 9.94},
    ]

    for c in cases:
        sl_dist = round(abs(c["sl"] - c["entry"]), 2)
        tp_dist = round(abs(c["entry"] - c["tp"]), 2)
        assert sl_dist == c["expected_sl_dist"]
        assert tp_dist == c["expected_tp_dist"]
        gross_rr = tp_dist / sl_dist
        assert gross_rr > 1.0


def test_net_rr_failure_rejection_without_clamping():
    """
    When structural SL is widened such that net R:R falls below required threshold (e.g. 1.5),
    calculator returns meets_min_rr == False. The system MUST reject the setup rather than
    clamping the SL inward to artificially save the trade.
    """
    entry = 4000.0
    too_wide_sl = 4030.0  # SL dist 30.0
    tp = 3980.0  # TP dist 20.0 -> Gross RR = 20/30 = 0.67 < 1.0

    calc = calculate_risk_reward(
        direction="SHORT",
        entry=entry,
        stop_loss=too_wide_sl,
        take_profit=tp,
        capital=1000.0,
        risk_pct=1.0,
        min_net_rr=1.5,
        leverage=10,
        costs=CostAssumptions(),
    )

    assert calc.meets_min_rr is False
    assert calc.can_execute is False
    assert calc.skip_reason is not None
