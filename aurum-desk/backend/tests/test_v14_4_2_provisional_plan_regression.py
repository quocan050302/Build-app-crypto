"""
backend/tests/test_v14_4_2_provisional_plan_regression.py
Regression tests for V14.4.2 provisional target selection, display plan DTO,
and numeric cost verification from user screenshots (T01–T08, T17–T20).
"""

import json
import pytest
from domain_calculator import calculate_risk_reward, validate_price_geometry, CostAssumptions
from services.provisional_plan_service import build_provisional_plan, adapt_legacy_liquidity_candidates
from services.setup_display_plan_service import build_setup_display_plan
from services.plan_resolver import resolve_plan_levels
from services.strategy_service import StrategyService
import models


def test_t01_screenshot1_short_inverted_tp_invalid():
    """T01 / TC-SCREENSHOT1-INVALID: SHORT entry 4178.16, SL 4186.50, lastLow 4179 -> invalid/no target, no fake RR."""
    res = build_provisional_plan(
        direction="SHORT",
        entry=4178.16,
        stop_loss=4186.50,
        levels=[{"price": 4179.00, "type": "SWING_LOW", "timestamp": 1000, "confirmed_at": 1000}],
        as_of_ms=2000
    )
    assert res.status == "NO_TARGET"
    assert res.reason_code == "NO_VALID_TARGET"
    assert res.take_profit is None
    assert res.calculation is None


def test_t02_prov_causal_target_below_entry():
    """T02 / TC-PROV-CAUSAL-TARGET: Level 4170 untouched known before cutoff -> target correct below entry."""
    res = build_provisional_plan(
        direction="SHORT",
        entry=4178.16,
        stop_loss=4186.50,
        levels=[
            {"price": 4179.00, "type": "SWING_LOW", "timestamp": 1000, "confirmed_at": 1000},
            {"price": 4170.00, "type": "SWING_LOW", "timestamp": 1000, "confirmed_at": 1000}
        ],
        as_of_ms=2000
    )
    assert res.status in ("AVAILABLE", "AVAILABLE_BLOCKED")
    assert res.take_profit == 4170.00
    assert res.take_profit < res.entry < res.stop_loss


def test_t03_prov_consumed_and_future_levels_rejected():
    """T03 / TC-PROV-CONSUMED-FUTURE: Consumed level and future-known level are not selected -> NO_TARGET."""
    res = build_provisional_plan(
        direction="SHORT",
        entry=4178.16,
        stop_loss=4186.50,
        levels=[
            # Level 4170 is BROKEN
            {"price": 4170.00, "type": "SWING_LOW", "timestamp": 1000, "confirmed_at": 1000, "state": "BROKEN"},
            # Level 4160 is in the future relative to as_of_ms (3000 > 2000)
            {"price": 4160.00, "type": "SWING_LOW", "timestamp": 3000, "confirmed_at": 3000, "state": "UNTOUCHED"}
        ],
        as_of_ms=2000
    )
    assert res.status == "NO_TARGET"
    assert res.take_profit is None


def test_t04_prov_long_mirror_rejected():
    """T04 / TC-PROV-LONG-MIRROR: LONG mirror (entry 4180 / sl 4170 / lastHigh 4179) -> rejected, NO_TARGET."""
    res = build_provisional_plan(
        direction="LONG",
        entry=4180.00,
        stop_loss=4170.00,
        levels=[{"price": 4179.00, "type": "SWING_HIGH", "timestamp": 1000, "confirmed_at": 1000}],
        as_of_ms=2000
    )
    assert res.status == "NO_TARGET"
    assert res.take_profit is None


def test_t05_confirmed_atomic_partial_rejected():
    """T05 / TC-PROV-CONFIRMED-ATOMIC: Confirmed levels missing TP does not mix with provisional TP."""
    setup = {
        "id": "watch-partial",
        "direction": "SHORT",
        "confirmed_entry": 4178.16,
        "confirmed_sl": 4186.50,
        "confirmed_tp": None,  # Missing confirmed TP
        "provisional_entry": 4178.16,
        "provisional_sl": 4186.50,
        "provisional_tp": 4150.00,
    }
    resolution = resolve_plan_levels(setup)
    assert resolution.is_valid is False
    assert resolution.error_code == "CONFIRMED_LEVELS_INCOMPLETE"
    assert resolution.take_profit is None


def test_t06_existing_armed_paper_open_frozen(isolated_db):
    db = isolated_db
    """T06 / TC-PROV-FROZEN-ARMED-OPEN: ARMED and PAPER_OPEN watch setup levels are frozen."""
    setup = models.WatchSetup(
        id="watch-frozen-armed",
        direction="SHORT",
        state="ARMED",
        provisional_entry=4178.16,
        provisional_sl=4186.50,
        provisional_tp=4160.00,
        confirmed_entry=4178.16,
        confirmed_sl=4186.50,
        confirmed_tp=4160.00,
        invalidation_price=4186.50,
        created_at=1000,
        updated_at=1000
    )
    db.add(setup)
    db.commit()

    # Verify that plan resolution uses confirmed levels
    res = resolve_plan_levels(setup)
    assert res.is_valid is True
    assert res.level_source == "confirmed"
    assert res.take_profit == 4160.00


def test_t07_upcoming_api_resolved_dto(client, isolated_db):
    db = isolated_db
    """T07 / TC-PROV-API-RESOLVED-DTO: /api/v1/setups/upcoming returns resolved_display_plan."""
    setup = models.WatchSetup(
        id="watch-api-test",
        direction="SHORT",
        state="WAITING_PRICE",
        provisional_entry=4178.16,
        provisional_sl=4186.50,
        provisional_tp=4179.00,  # Inverted geometry
        invalidation_price=4186.50,
        created_at=1000,
        updated_at=1000
    )
    db.add(setup)
    db.commit()

    resp = client.get("/api/v1/setups/upcoming")
    assert resp.status_code == 200
    data = resp.json()
    item = next((s for s in data["setups"] if s["id"] == "watch-api-test"), None)
    assert item is not None
    assert "resolved_display_plan" in item
    dto = item["resolved_display_plan"]
    assert dto["is_valid"] is False
    assert dto["take_profit"] is None
    assert dto["financial"] is None
    assert "diagnostic_prices" in dto


def test_t08_ui_display_plan_dto_no_fake_defaults():
    """T08 / TC-PROV-UI-DISPLAY-DTO: Invalid plan has take_profit=None and financial=None."""
    setup_dict = {
        "id": "watch-XAUUSDT-15M",
        "direction": "SHORT",
        "provisional_entry": 4178.16,
        "provisional_sl": 4186.50,
        "provisional_tp": 4179.00,
        "state": "WAITING_PRICE",
        "risk_usdt": 0.0,
        "quantity": 0.0,
        "net_rr": 0.0,
    }
    plan = build_setup_display_plan(setup_dict)
    assert plan["is_valid"] is False
    assert plan["take_profit"] is None
    assert plan["financial"] is None
    assert plan["status"] == "INVALID_GEOMETRY"


def test_t17_screenshot2_draft_exact_calculations():
    """T17 / TC-SCREENSHOT2-NUMERIC: Exact calculations matching screenshot 2.
    SHORT Entry 4179.18, SL 4184.45, TP 4159.18, Q 0.28, capital 1000, risk 0.25%, leverage 20.
    """
    calc = calculate_risk_reward(
        direction="SHORT",
        planned_entry=4179.18,
        stop_loss=4184.45,
        take_profit=4159.18,
        quantity_override=0.28,
        capital=1000.0,
        risk_pct=0.25,
        leverage=20,
        margin_mode="ISOLATED"
    )
    assert calc.is_valid is True
    # Gross loss: 0.28 * (4184.45 - 4179.18) = 0.28 * 5.27 = 1.4756 -> ~1.48
    assert round(calc.gross_loss_usdt, 2) == 1.48
    # Gross reward: 0.28 * (4179.18 - 4159.18) = 0.28 * 20.0 = 5.60
    assert round(calc.gross_reward_usdt, 2) == 5.60
    # Net risk: gross loss (1.4756) + total fees/slippage (~0.99) = ~2.47
    assert round(calc.net_risk_usdt, 2) == 2.47
    # Net reward: gross reward (5.60) - total fees/slippage (~0.96) = ~4.64
    assert round(calc.net_reward_usdt, 2) == 4.64
    # Gross RR: 20.0 / 5.27 = 3.7951
    assert round(calc.gross_rr, 4) == 3.7951
    # Net RR: 4.64 / 2.47 = 1.879
    assert round(calc.net_rr, 3) == 1.879
    assert calc.meets_min_rr is False


def test_t18_screenshot1_zero_financial_estimate():
    """T18 / TC-SCREENSHOT1-ZERO-ESTIMATE: Inverted geometry yields is_valid=False and no execution."""
    calc = calculate_risk_reward(
        direction="SHORT",
        planned_entry=4178.16,
        stop_loss=4186.50,
        take_profit=4179.00,
        capital=1000.0,
        risk_pct=0.25,
        leverage=20
    )
    assert calc.is_valid is False
    assert calc.can_execute is False
    assert "INVALID_SHORT_GEOMETRY" in calc.invalid_reason


def test_t19_signed_rr_no_math_abs_positive():
    """T19 / TC-SIGNED-RR-NO-ABS: Negative reward distance is never abs'd into a positive TP profit."""
    valid, reason = validate_price_geometry("SHORT", 4178.16, 4186.50, 4179.00)
    assert valid is False
    assert "TP" in reason and "Entry" in reason

    valid_long, reason_long = validate_price_geometry("LONG", 4180.00, 4170.00, 4175.00)
    assert valid_long is False
    assert "TP" in reason_long and "Entry" in reason_long


def test_t20_cost_snapshot_consistency():
    """T20 / TC-COST-SNAPSHOT-CONSISTENCY: Both long and short calculations use authentic cost snapshot without artificial fee inflation."""
    costs = CostAssumptions(
        maker_fee=0.0002,
        taker_fee=0.0006,
        slippage_pct=0.0005,
        multiplier=1.0
    )
    calc_short = calculate_risk_reward(
        direction="SHORT",
        planned_entry=4179.18,
        stop_loss=4184.45,
        take_profit=4159.18,
        quantity_override=0.28,
        costs=costs
    )
    calc_long = calculate_risk_reward(
        direction="LONG",
        planned_entry=4159.18,
        stop_loss=4153.91,
        take_profit=4179.18,
        quantity_override=0.28,
        costs=costs
    )
    assert calc_short.is_valid is True
    assert calc_long.is_valid is True
    assert calc_short.multiplier == 1.0
    assert calc_long.multiplier == 1.0
    assert calc_short.net_risk_usdt > 0
    assert calc_long.net_risk_usdt > 0
