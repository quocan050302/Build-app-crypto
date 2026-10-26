"""
Aurum V17.5.1 — Stop Costs & Sizing Invariants Tests
Verifies that wider structural stops (Variant C) proportionally reduce quantity,
preserving the authoritative risk budget without budget inflation.
"""
import pytest
from domain_calculator import calculate_risk_reward, CostAssumptions


def test_wider_structural_stop_reduces_quantity_within_budget():
    capital = 1000.0
    risk_pct = 0.25  # 0.25% of 1000 = 2.50 USDT budget
    entry = 2000.0
    tp = 1960.0
    costs = CostAssumptions(fee_pct=0.0004, slippage_ticks=1.0, multiplier=1.0)

    # Variant A (Narrow stop loss: distance 5.0)
    calc_a = calculate_risk_reward(
        direction="SHORT",
        entry=entry,
        stop_loss=2005.0,
        take_profit=tp,
        capital=capital,
        risk_pct=risk_pct,
        leverage=5,
        costs=costs,
        now_ms=1000000,
    )
    assert calc_a.can_execute

    # Variant C (Wider structural stop: distance 10.0)
    calc_c = calculate_risk_reward(
        direction="SHORT",
        entry=entry,
        stop_loss=2010.0,
        take_profit=tp,
        capital=capital,
        risk_pct=risk_pct,
        leverage=5,
        costs=costs,
        now_ms=1000000,
    )
    assert calc_c.can_execute

    # Invariants:
    # 1. Quantity of C must be strictly less than quantity of A
    assert calc_c.quantity < calc_a.quantity, f"Expected qty_c < qty_a, got {calc_c.quantity} vs {calc_a.quantity}"

    # 2. Both net risks must be strictly <= budget (2.50 USDT) + lot-step tolerance
    assert calc_a.net_risk_usdt <= 2.55, f"Risk A exceeded budget: {calc_a.net_risk_usdt}"
    assert calc_c.net_risk_usdt <= 2.55, f"Risk C exceeded budget: {calc_c.net_risk_usdt}"

    # 3. Budget is never inflated for C
    assert abs(calc_c.budget_usdt - calc_a.budget_usdt) < 1e-4
