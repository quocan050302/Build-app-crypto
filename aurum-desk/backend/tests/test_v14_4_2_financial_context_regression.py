"""
backend/tests/test_v14_4_2_financial_context_regression.py
Regression tests for unified plan financial context (FIN01–FIN04).
Enforces:
- FIN01: DayAudit equity 2000 used for sizing, not hardcoded 1000.
- FIN02: None financial fields handled without TypeError or fake 0.
- FIN03: Upcoming API returns degraded rows without omitting setups.
- FIN04: Capital variants (500, 1000, 2000) and LONG mirror scaling.
"""

import pytest
from domain_calculator import CostAssumptions
from services.plan_financial_context_service import (
    PlanFinancialContext,
    calculate_display_financial,
)
from services.setup_display_plan_service import build_setup_display_plan


def _make_context(
    equity: float = 1000.0,
    risk_pct: float = 0.25,
    leverage: int = 20,
    margin_mode: str = "ISOLATED",
    min_net_rr: float = 2.0,
) -> PlanFinancialContext:
    costs = CostAssumptions(
        maker_fee_rate=0.0002,
        taker_fee_rate=0.0004,
        slippage_usd=0.10,
        multiplier=1.0,
    )
    return PlanFinancialContext(
        equity_usdt=equity,
        equity_basis="DAY_AUDIT_BALANCE",
        equity_as_of_ms=1728000000000,
        risk_pct=risk_pct,
        leverage=leverage,
        margin_mode=margin_mode,
        risk_config_version=1,
        costs=costs,
        cost_version="v14.4_test_costs",
        instrument_version="XAUUSDT-test-v1",
        min_net_rr=min_net_rr,
    )


def test_fin01_day_audit_equity_2000_sizing():
    """FIN01 / TC-FIN-EQUITY-2000: When DayAudit equity is 2000, sizing uses 2000, not hardcoded 1000.
    SHORT Entry 4179.18, SL 4184.45, TP 4159.18, risk 0.25%, leverage 20.
    At 1000: risk budget = $2.50 -> Q ~0.28, net_risk ~2.47.
    At 2000: risk budget = $5.00 -> Q ~0.56, net_risk ~4.94.
    """
    setup = {
        "id": "watch-fin01-short",
        "direction": "SHORT",
        "provisional_entry": 4179.18,
        "provisional_sl": 4184.45,
        "provisional_tp": 4159.18,
        "state": "WATCHING",
        "net_rr": None,
        "quantity": None,
        "risk_usdt": None,
    }

    ctx_1000 = _make_context(equity=1000.0)
    plan_1000 = build_setup_display_plan(setup, financial_context=ctx_1000)
    assert plan_1000["is_valid"] is True
    assert plan_1000["financial"] is not None
    assert round(plan_1000["financial"]["quantity"], 2) == 0.28
    assert round(plan_1000["financial"]["net_risk_usdt"], 2) == 2.47
    assert plan_1000["financial"]["equity_usdt"] == 1000.0

    ctx_2000 = _make_context(equity=2000.0)
    plan_2000 = build_setup_display_plan(setup, financial_context=ctx_2000)
    assert plan_2000["is_valid"] is True
    assert plan_2000["financial"] is not None
    # Sizing must double with double equity
    assert round(plan_2000["financial"]["quantity"], 2) == 0.56
    assert round(plan_2000["financial"]["net_risk_usdt"], 2) == 4.94
    assert plan_2000["financial"]["equity_usdt"] == 2000.0
    # Must not match the hardcoded 1000 capital figures
    assert plan_2000["financial"]["quantity"] != plan_1000["financial"]["quantity"]


def test_fin02_none_financial_fields_no_type_error():
    """FIN02 / TC-FIN-NONE-FIELDS: None net_rr/quantity/risk_usdt does not raise TypeError."""
    setup_none = {
        "id": "watch-fin02-none",
        "direction": "SHORT",
        "provisional_entry": 4179.18,
        "provisional_sl": 4184.45,
        "provisional_tp": 4159.18,
        "state": "WATCHING",
        "net_rr": None,
        "gross_rr": None,
        "quantity": None,
        "risk_usdt": None,
        "leverage": None,
        "margin_mode": None,
    }

    # Case A: with context -> computes successfully without crashing on None comparisons
    ctx = _make_context(equity=1500.0)
    plan = build_setup_display_plan(setup_none, financial_context=ctx)
    assert plan["is_valid"] is True
    assert plan["financial"] is not None
    assert plan["financial"]["quantity"] > 0
    assert plan["financial"]["net_rr"] > 0

    # Case B: without context -> returns financial: None and CONTEXT_UNAVAILABLE without crashing
    plan_no_ctx = build_setup_display_plan(setup_none, financial_context=None)
    assert plan_no_ctx["is_valid"] is True
    assert plan_no_ctx["financial"] is None
    assert plan_no_ctx["status"] == "CONTEXT_UNAVAILABLE"


def test_fin03_upcoming_api_preserves_degraded_and_valid_rows(client, isolated_db):
    """FIN03 / TC-FIN-API-DEGRADED-ROWS: Upcoming API returns degraded rows without omitting setups."""
    import models

    # Clean existing test watch setups in isolated db
    isolated_db.query(models.WatchSetup).delete()
    isolated_db.commit()

    # Seed one valid setup
    s_good = models.WatchSetup(
        id="watch-good-1",
        direction="SHORT",
        state="WATCHING",
        provisional_entry=4179.18,
        provisional_sl=4184.45,
        provisional_tp=4159.18,
        invalidation_price=4184.45,
        created_at=1000,
        updated_at=1000,
    )

    # Seed one degraded setup with inverted geometry
    s_bad = models.WatchSetup(
        id="watch-bad-2",
        direction="SHORT",
        state="WATCHING",
        provisional_entry=4178.16,
        provisional_sl=4186.50,
        provisional_tp=4179.00,  # Inverted SHORT TP
        invalidation_price=4186.50,
        created_at=1001,
        updated_at=1001,
    )

    isolated_db.add(s_good)
    isolated_db.add(s_bad)
    isolated_db.commit()

    resp = client.get("/api/v1/setups/upcoming")
    assert resp.status_code == 200
    data = resp.json()
    setups = data.get("setups", [])

    # Both rows must be returned
    good_item = next((s for s in setups if s["id"] == "watch-good-1"), None)
    bad_item = next((s for s in setups if s["id"] == "watch-bad-2"), None)

    assert good_item is not None, "Good setup must not be skipped"
    assert bad_item is not None, "Bad setup must not be dropped by continue"

    # Good setup has valid plan
    assert good_item["resolved_display_plan"]["is_valid"] is True
    assert good_item["resolved_display_plan"]["take_profit"] == 4159.18
    assert good_item["resolved_display_plan"]["financial"] is not None

    # Bad setup has invalid plan with diagnostic prices and financial None
    assert bad_item["resolved_display_plan"]["is_valid"] is False
    assert bad_item["resolved_display_plan"]["take_profit"] is None
    assert bad_item["resolved_display_plan"]["financial"] is None


def test_fin04_capital_variants_and_long_mirror():
    """FIN04 / TC-FIN-CAPITAL-VARIANTS: Tests capitals 500, 1000, 2000 and LONG mirror."""
    # SHORT
    entry = 4179.18
    sl = 4184.45
    tp = 4159.18

    c500 = calculate_display_financial("SHORT", entry, sl, tp, _make_context(equity=500.0))
    c1000 = calculate_display_financial("SHORT", entry, sl, tp, _make_context(equity=1000.0))
    c2000 = calculate_display_financial("SHORT", entry, sl, tp, _make_context(equity=2000.0))

    assert c500.is_valid and c1000.is_valid and c2000.is_valid
    # Quantity scales with capital: 500 -> ~0.14, 1000 -> ~0.28, 2000 -> ~0.56
    assert round(c500.quantity, 2) == 0.14
    assert round(c1000.quantity, 2) == 0.28
    assert round(c2000.quantity, 2) == 0.56
    # Net risk scales: 500 -> ~1.23, 1000 -> ~2.47, 2000 -> ~4.94
    assert round(c500.net_risk_usdt, 2) == 1.23
    assert round(c1000.net_risk_usdt, 2) == 2.47
    assert round(c2000.net_risk_usdt, 2) == 4.94

    # LONG Mirror: Entry 4170.00, SL 4165.00, TP 4185.00
    long_calc = calculate_display_financial("LONG", 4170.00, 4165.00, 4185.00, _make_context(equity=1000.0))
    assert long_calc.is_valid is True
    assert long_calc.gross_rr == 3.0
    assert long_calc.net_rr > 0
    assert long_calc.net_reward_usdt > 0
