"""
backend/tests/e2e_financial_helper.py
V16.39.21 — Pure helper to calculate canonical plan financials for E2E fixtures
using authoritative domain_calculator.calculate_risk_reward.
"""
from typing import Optional
from domain_calculator import (
    CalculationResult,
    CostAssumptions,
    calculate_risk_reward,
)


def _calculate_e2e_plan_financials(
    direction: str,
    entry: float,
    sl: float,
    tp: float,
    *,
    capital: float = 1000.0,
    risk_pct: float = 0.25,
    min_net_rr: float = 1.5,
    leverage: int = 5,
    margin_mode: str = "ISOLATED",
    costs: Optional[CostAssumptions] = None,
) -> CalculationResult:
    """Authoritatively computes canonical financial metrics for E2E fixtures."""
    result = calculate_risk_reward(
        direction=direction,
        entry=entry,
        sl=sl,
        tp=tp,
        capital=capital,
        risk_pct=risk_pct,
        min_net_rr=min_net_rr,
        costs=costs if costs is not None else CostAssumptions(),
        entry_has_slippage=False,
        leverage=leverage,
        margin_mode=margin_mode,
    )
    if not result.is_valid:
        raise ValueError(
            f"E2E plan invalid: {direction} "
            f"entry={entry}, sl={sl}, tp={tp}: {result.invalid_reason}"
        )
    return result
