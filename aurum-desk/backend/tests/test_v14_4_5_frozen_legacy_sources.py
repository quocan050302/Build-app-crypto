"""
backend/tests/test_v14_4_5_frozen_legacy_sources.py
V14.4.5 — Case C01: Frozen Source Geometry & Legacy Adaptation Integrity.

Covers PHẦN 031–036, 092–093, 111:
1. Case C01:
   - ARMED SHORT setup with provisional levels: entry=4180.0, SL=4190.0, TP=4160.0.
   - Frozen snapshot is strictly financial-only (net_rr=2.84, quantity=0.18, no entry/sl/tp).
   - Plan must be evaluated as degraded/incomplete:
     * is_valid is False
     * reason_code is 'INCOMPLETE_FROZEN_SOURCE'
     * financial is None (no fake numbers, no executable RR overlay)
     * does NOT borrow provisional levels from the current setup.
2. Empty dict `{}` or None frozen snapshot in FROZEN_ORDER mode:
   - Evaluates as INCOMPLETE_FROZEN_SOURCE.
3. Missing actual_entry in PAPER_OPEN / CLOSED:
   - Does NOT borrow planned_entry and falsely tag it ACTUAL_FILL.
4. Same-order row rebuild:
   - Authoritative PaperOrder row containing actual_entry, stop_loss, take_profit builds valid canonical plan.
5. Setup changes after arming do NOT mutate order source.
6. Geometry validation:
   - Strict validation: LONG requires SL < entry < TP; SHORT requires TP < entry < SL.
   - Inverted levels return INVALID_GEOMETRY without abs() salvage.
"""
import pytest
from services.setup_display_plan_service import (
    build_setup_display_plan,
    select_authoritative_plan_source,
    validate_selected_plan,
    build_order_row_display_plan,
    select_order_row_plan_source,
    PlanFinancialContext,
)
from domain_calculator import CostAssumptions


def test_c01_financial_only_frozen_snapshot_is_incomplete_and_unexecutable():
    """Case C01 (PHẦN 031, 111):
    ARMED SHORT setup 4180/4190/4160 with financial-only snapshot (net_rr=2.84, qty=0.18).
    Must evaluate to INCOMPLETE_FROZEN_SOURCE without borrowing provisional setup levels.
    """
    setup = {
        "id": "setup-arm-short-c01",
        "direction": "SHORT",
        "provisional_entry": 4180.0,
        "provisional_sl": 4190.0,
        "provisional_tp": 4160.0,
        "state": "ARMED",
        "version": 1,
    }
    financial_only_snapshot = {
        "net_rr": 2.84,
        "quantity": 0.18,
        "equity_usdt": 1000.0,
        "risk_pct": 0.5,
    }

    plan = build_setup_display_plan(
        setup,
        pricing_mode="FROZEN_ORDER",
        frozen_snapshot=financial_only_snapshot,
    )

    # Must be marked incomplete and invalid
    assert plan["is_valid"] is False
    assert plan["reason_code"] == "INCOMPLETE_FROZEN_SOURCE"
    assert "INCOMPLETE_FROZEN_SOURCE" in plan["status"]
    # Does NOT borrow provisional entry/sl/tp from setup
    assert plan["entry"] is None
    assert plan["stop_loss"] is None
    assert plan["take_profit"] is None
    # Financial block must be None (no fake defaults)
    assert plan["financial"] is None


def test_empty_dict_and_none_frozen_snapshot_are_incomplete():
    """PHẦN 031, 092: Empty dict or None frozen snapshot returns INCOMPLETE_FROZEN_SOURCE."""
    setup = {
        "id": "setup-empty-snap",
        "direction": "LONG",
        "provisional_entry": 4180.0,
        "provisional_sl": 4170.0,
        "provisional_tp": 4200.0,
        "state": "ARMED",
    }

    # 1. Empty dict
    plan_empty = build_setup_display_plan(
        setup,
        pricing_mode="FROZEN_ORDER",
        frozen_snapshot={},
    )
    assert plan_empty["is_valid"] is False
    assert plan_empty["reason_code"] == "INCOMPLETE_FROZEN_SOURCE"
    assert plan_empty["entry"] is None

    # 2. None snapshot in FROZEN_ORDER mode
    plan_none = build_setup_display_plan(
        setup,
        pricing_mode="FROZEN_ORDER",
        frozen_snapshot=None,
    )
    assert plan_none["is_valid"] is False
    assert plan_none["reason_code"] == "INCOMPLETE_FROZEN_SOURCE"


def test_missing_actual_in_open_order_does_not_forge_actual_fill_basis():
    """PHẦN 033: Missing actual_entry in PAPER_OPEN or CLOSED does not take planned and call it ACTUAL_FILL."""
    setup = {
        "id": "setup-open-missing-actual",
        "direction": "LONG",
        "state": "PAPER_OPEN",
    }
    # Snapshot has planned_entry but NO actual_entry
    frozen_snap = {
        "planned_entry": 4180.0,
        "stop_loss": 4170.0,
        "take_profit": 4200.0,
        "actual_entry": None,
    }

    plan = build_setup_display_plan(
        setup,
        pricing_mode="FROZEN_ORDER",
        frozen_snapshot=frozen_snap,
    )
    # Because order is PAPER_OPEN and actual_entry is None, entry cannot be assumed as ACTUAL_FILL
    assert plan["is_valid"] is False
    assert plan["reason_code"] == "INCOMPLETE_FROZEN_SOURCE"
    assert plan["price_basis"] != "ACTUAL_FILL"


def test_same_order_row_rebuild_with_provenance_is_valid():
    """PHẦN 032: PaperOrder row with valid actual/planned geometry can build valid canonical plan without WatchSetup."""
    order_row = {
        "id": "ord-recon-valid-01",
        "direction": "LONG",
        "state": "PAPER_OPEN",
        "planned_entry": 4180.0,
        "actual_entry": 4181.0,
        "stop_loss": 4170.0,
        "take_profit": 4200.0,
        "opened_at": 1728000000000,
    }

    plan = build_order_row_display_plan(order_row)
    assert plan["is_valid"] is True
    assert plan["entry"] == 4181.0
    assert plan["stop_loss"] == 4170.0
    assert plan["take_profit"] == 4200.0
    assert plan["price_basis"] == "ACTUAL_FILL"
    assert plan["identity"]["order_id"] == "ord-recon-valid-01"


def test_setup_changes_after_arming_do_not_mutate_frozen_order():
    """PHẦN 035, 146: When setup provisional levels change after arming, the frozen order plan is unaffected."""
    complete_frozen_snap = {
        "planned_entry": 4180.0,
        "stop_loss": 4185.0,
        "take_profit": 4165.0,
        "price_basis": "PLANNED_LIMIT",
        "net_rr": 2.1,
        "quantity": 0.5,
    }
    # Current setup evolved to new levels
    changed_setup = {
        "id": "setup-changed-01",
        "direction": "SHORT",
        "provisional_entry": 4250.0,
        "provisional_sl": 4260.0,
        "provisional_tp": 4220.0,
        "state": "ARMED",
    }

    plan = build_setup_display_plan(
        changed_setup,
        pricing_mode="FROZEN_ORDER",
        frozen_snapshot=complete_frozen_snap,
    )
    assert plan["is_valid"] is True
    assert plan["entry"] == 4180.0
    assert plan["stop_loss"] == 4185.0
    assert plan["take_profit"] == 4165.0
    # Must NOT have taken the new 4250/4260/4220 from setup
    assert plan["entry"] != 4250.0


def test_geometry_validation_rejects_inverted_rr_without_abs():
    """PHẦN 034: Inverted geometry (e.g. LONG with SL > entry) is rejected without abs() rescue."""
    # Inverted LONG: SL (4190) > entry (4180)
    source_inverted_long = select_authoritative_plan_source(
        setup={"id": "s1", "direction": "LONG", "state": "ARMED"},
        pricing_mode="FROZEN_ORDER",
        frozen_snapshot={
            "planned_entry": 4180.0,
            "stop_loss": 4190.0,  # Invalid for LONG!
            "take_profit": 4200.0,
            "direction": "LONG",
        },
    )
    val_long = validate_selected_plan(source_inverted_long)
    assert val_long.is_valid is False
    assert val_long.reason_code == "INVALID_GEOMETRY"

    # Inverted SHORT: SL (4170) < entry (4180)
    source_inverted_short = select_authoritative_plan_source(
        setup={"id": "s2", "direction": "SHORT", "state": "ARMED"},
        pricing_mode="FROZEN_ORDER",
        frozen_snapshot={
            "planned_entry": 4180.0,
            "stop_loss": 4170.0,  # Invalid for SHORT!
            "take_profit": 4160.0,
            "direction": "SHORT",
        },
    )
    val_short = validate_selected_plan(source_inverted_short)
    assert val_short.is_valid is False
    assert val_short.reason_code == "INVALID_GEOMETRY"
