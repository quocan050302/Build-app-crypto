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


def test_fin01_db_equity_loader_clock_controlled(isolated_db):
    """FIN01 / TC-FIN-LOADER: Test load_plan_financial_context from real DayAudit in DB,
    ensuring equity_as_of_ms is the equity record timestamp, not cooldown_until.
    """
    import models
    from services.clock import FakeClock
    from services.plan_financial_context_service import load_plan_financial_context

    clock = FakeClock(1728005000000)
    audit = models.DayAudit(
        date_str=clock.get_today_str_vn(),
        initial_equity=2500.0,
        current_equity=2500.0,
        cooldown_until=1728099999000,
        updated_at_ms=1728001000000,
    )
    isolated_db.add(audit)
    isolated_db.commit()

    clock = FakeClock(1728005000000)
    ctx = load_plan_financial_context(isolated_db, clock=clock)
    assert ctx.equity_usdt == 2500.0
    assert ctx.equity_basis == "DAY_AUDIT_BALANCE"
    # Must NOT equal cooldown_until!
    assert ctx.equity_as_of_ms == 1728001000000
    assert ctx.equity_as_of_ms != 1728099999000


def test_fin02_row_leverage_vs_current_context():
    """FIN02 / TC-FIN-LABEL: Setup row has leverage 5, risk_pct 0.1,
    but current context has leverage 20, risk_pct 0.25, equity 2000.
    Preview plan leverage and sizing MUST match the calculator context (20), NOT the old row (5).
    """
    setup_old_row = {
        "id": "watch-fin02-leverage-check",
        "direction": "SHORT",
        "provisional_entry": 4179.18,
        "provisional_sl": 4184.45,
        "provisional_tp": 4159.18,
        "state": "WATCHING",
        "leverage": 5,
        "risk_pct": 0.1,
    }

    ctx = _make_context(equity=2000.0, leverage=20, risk_pct=0.25)
    plan = build_setup_display_plan(setup_old_row, financial_context=ctx)

    assert plan["is_valid"] is True
    assert plan["financial"] is not None
    # Sizing and label must come from current context (leverage 20, equity 2000)
    assert plan["financial"]["leverage"] == 20
    assert plan["financial"]["risk_pct"] == 0.25
    assert plan["financial"]["equity_usdt"] == 2000.0
    assert round(plan["financial"]["quantity"], 2) == 0.56


def test_fin03_complete_fee_reward_dto_parity():
    """FIN03 / TC-FIN-FEES: Ensures full financial DTO fields are populated,
    matching Appendix D requirements, and deterministic fingerprint is computed.
    """
    setup = {
        "id": "watch-fin03-full-dto",
        "direction": "SHORT",
        "provisional_entry": 4179.18,
        "provisional_sl": 4184.45,
        "provisional_tp": 4159.18,
        "state": "WATCHING",
    }
    ctx = _make_context(equity=2000.0, leverage=20, risk_pct=0.25)
    plan = build_setup_display_plan(setup, financial_context=ctx)

    assert plan["is_valid"] is True
    fin = plan["financial"]
    assert fin is not None

    # Check complete fee and reward fields
    assert "gross_loss_usdt" in fin and fin["gross_loss_usdt"] is not None
    assert "gross_reward_usdt" in fin and fin["gross_reward_usdt"] is not None
    assert "net_reward_usdt" in fin and fin["net_reward_usdt"] is not None
    assert "net_risk_usdt" in fin and fin["net_risk_usdt"] is not None
    assert "entry_fee_usdt" in fin and fin["entry_fee_usdt"] > 0
    assert "sl_exit_fee_usdt" in fin and fin["sl_exit_fee_usdt"] > 0
    assert "tp_exit_fee_usdt" in fin and fin["tp_exit_fee_usdt"] > 0
    assert "entry_slippage_usdt" in fin
    assert "sl_exit_slippage_usdt" in fin
    assert "tp_exit_slippage_usdt" in fin
    assert "pricing_mode" in fin and fin["pricing_mode"] == "CURRENT_PREVIEW"
    # Net RR is ~1.88 < min_net_rr (2.0), so financial_status is AVAILABLE_BLOCKED
    assert "financial_status" in fin and fin["financial_status"] == "AVAILABLE_BLOCKED"
    assert "fingerprint" in plan and len(plan["fingerprint"]) == 16


def test_fin04_frozen_order_missing_values_stay_null_no_contamination():
    """FIN04 / TC-FIN-FROZEN-NULLS: Frozen order with missing quantity/risk keeps them None,
    financial_status is FINANCIAL_UNAVAILABLE, and does NOT attach current context equity.
    """
    frozen_setup_incomplete = {
        "id": "watch-fin04-frozen-incomplete",
        "direction": "SHORT",
        "confirmed_entry": 4179.18,
        "confirmed_sl": 4184.45,
        "confirmed_tp": 4159.18,
        "state": "ARMED",
        "net_rr": None,
        "gross_rr": None,
        "quantity": None,
        "risk_usdt": None,
    }

    # Pass today's equity 3000 context
    today_ctx = _make_context(equity=3000.0)
    plan = build_setup_display_plan(frozen_setup_incomplete, financial_context=today_ctx)

    assert plan["status"] == "FINANCIAL_UNAVAILABLE"
    assert plan["reason_code"] == "ORDER_FINANCIAL_UNAVAILABLE"
    fin = plan["financial"]
    assert fin is not None
    assert fin["net_rr"] is None
    assert fin["quantity"] is None
    assert fin["net_risk_usdt"] is None
    # Must NOT contaminate with today's equity (3000)!
    assert fin["equity_usdt"] is None
    assert fin["cost_version"] is None
    assert fin["pricing_mode"] == "FROZEN_ORDER"
    assert fin["financial_status"] == "FINANCIAL_UNAVAILABLE"


def test_fin05_version_changes_affect_preview_not_frozen():
    """FIN05 / TC-FIN-VERSIONS: Changing cost/instrument version affects preview,
    while frozen order snapshot preserves its historical version.
    """
    frozen_setup_complete = {
        "id": "watch-fin05-frozen-complete",
        "direction": "SHORT",
        "confirmed_entry": 4179.18,
        "confirmed_sl": 4184.45,
        "confirmed_tp": 4159.18,
        "state": "CLOSED",
        "net_rr": 2.45,
        "gross_rr": 3.80,
        "quantity": 0.28,
        "risk_usdt": 2.47,
        "initial_equity": 1000.0,
        "cost_version": "v10.4_old_costs",
        "instrument_version": "XAUUSDT-old-v0",
    }

    new_ctx = _make_context(equity=5000.0)
    # Ensure new_ctx has distinct cost_version
    plan_frozen = build_setup_display_plan(frozen_setup_complete, financial_context=new_ctx)

    assert plan_frozen["status"] == "FROZEN_ORDER"
    fin = plan_frozen["financial"]
    assert fin["cost_version"] == "v10.4_old_costs"
    assert fin["instrument_version"] == "XAUUSDT-old-v0"
    assert fin["equity_usdt"] == 1000.0
    assert fin["quantity"] == 0.28
    # Preview with same context would have equity 5000
    preview_setup = {
        "id": "watch-fin05-preview",
        "direction": "SHORT",
        "provisional_entry": 4179.18,
        "provisional_sl": 4184.45,
        "provisional_tp": 4159.18,
        "state": "WATCHING",
    }
    plan_preview = build_setup_display_plan(preview_setup, financial_context=new_ctx)
    assert plan_preview["financial"]["equity_usdt"] == 5000.0
    assert plan_preview["financial"]["cost_version"] == new_ctx.cost_version
