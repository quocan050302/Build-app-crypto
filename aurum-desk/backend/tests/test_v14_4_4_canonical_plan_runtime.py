"""
backend/tests/test_v14_4_4_canonical_plan_runtime.py
V14.4.4 - Canonical Display Plan Runtime & Source Integrity Tests.

Proves:
1. Frozen ARMED snapshot is an atomic triple:
   Missing SL or TP in snapshot renders source incomplete (is_complete=False);
   does NOT borrow provisional levels from setup!
2. pricing_mode="FROZEN_ORDER" with snapshot=None produces an incomplete degraded plan;
   does NOT silently fall back to SETUP_PREVIEW.
3. Planned vs Actual entry basis:
   - ARMED uses planned_entry and price_basis "PLANNED_LIMIT".
   - OPEN/CLOSED uses actual_entry and price_basis "ACTUAL_FILL".
   - Missing actual_entry does NOT claim ACTUAL_FILL.
4. Valid historic order is not invalidated by current raw setup becoming invalid.
"""
from services.setup_display_plan_service import (
    select_authoritative_plan_source,
    build_setup_display_plan,
)


def test_frozen_armed_partial_triple_does_not_borrow_from_setup():
    """PHẦN 047: Frozen ARMED lacking SL or TP is incomplete and does not borrow setup levels."""
    raw_setup = {
        "id": "setup_part_1",
        "state": "ARMED",
        "direction": "LONG",
        "entry": 4180.0,
        "stop_loss": 4170.0,
        "take_profit": 4200.0,
        "provisional_sl": 4170.0,
        "provisional_tp": 4200.0,
    }

    # Frozen snapshot only has planned_entry, missing SL and TP
    partial_snapshot = {
        "order_id": "ord_part_1",
        "direction": "LONG",
        "planned_entry": 4180.0,
        # missing stop_loss and take_profit
    }

    selected = select_authoritative_plan_source(
        raw_setup,
        pricing_mode="FROZEN_ORDER",
        frozen_snapshot=partial_snapshot,
    )

    assert selected.source_kind == "FROZEN_ORDER"
    assert selected.is_complete is False
    assert "stop_loss" in selected.missing_fields
    assert "take_profit" in selected.missing_fields
    # Must NOT borrow setup SL/TP
    assert selected.stop_loss is None
    assert selected.take_profit is None


def test_frozen_order_mode_with_none_snapshot_degraded():
    """PHẦN 047: pricing_mode='FROZEN_ORDER' with snapshot=None is incomplete, not SETUP_PREVIEW."""
    raw_setup = {
        "id": "setup_none_1",
        "state": "WATCHING",
        "direction": "SHORT",
        "entry": 4180.0,
        "stop_loss": 4190.0,
        "take_profit": 4160.0,
    }

    selected = select_authoritative_plan_source(
        raw_setup,
        pricing_mode="FROZEN_ORDER",
        frozen_snapshot=None,
    )

    assert selected.source_kind == "FROZEN_ORDER"
    assert selected.is_complete is False
    assert "missing_frozen_snapshot" in selected.missing_fields

    # Display plan built from this is degraded
    plan = build_setup_display_plan(raw_setup, pricing_mode="FROZEN_ORDER", frozen_snapshot=None)
    assert plan["is_valid"] is False
    assert plan["reason_code"] in ("INCOMPLETE_FROZEN_SOURCE", "MISSING_FROZEN_SNAPSHOT")


def test_entry_basis_planned_vs_actual():
    """PHẦN 049: ARMED uses planned_entry; OPEN uses actual_entry."""
    raw_setup = {"id": "setup_basis_1", "state": "ARMED", "direction": "LONG"}
    armed_snapshot = {
        "order_id": "ord_armed",
        "direction": "LONG",
        "planned_entry": 4182.0,
        "stop_loss": 4172.0,
        "take_profit": 4202.0,
        "actual_entry": None,
    }
    sel_armed = select_authoritative_plan_source(raw_setup, frozen_snapshot=armed_snapshot)
    assert sel_armed.entry == 4182.0
    assert sel_armed.price_basis == "PLANNED_LIMIT"

    open_setup = {"id": "setup_basis_2", "state": "PAPER_OPEN", "direction": "LONG"}
    open_snapshot = {
        "order_id": "ord_open",
        "direction": "LONG",
        "planned_entry": 4182.0,
        "actual_entry": 4183.5,
        "stop_loss": 4172.0,
        "take_profit": 4202.0,
    }
    sel_open = select_authoritative_plan_source(open_setup, frozen_snapshot=open_snapshot)
    assert sel_open.entry == 4183.5
    assert sel_open.price_basis == "ACTUAL_FILL"


def test_valid_historic_order_not_blocked_by_invalid_current_setup():
    """PHẦN 048, 055: Valid frozen order remains valid even if current setup has invalid levels."""
    invalid_setup = {
        "id": "setup_inv",
        "state": "CLOSED",
        "direction": "LONG",
        "entry": None,
        "stop_loss": 0,
        "take_profit": -5,
    }
    valid_order_snapshot = {
        "order_id": "ord_hist_valid",
        "direction": "LONG",
        "planned_entry": 4180.0,
        "actual_entry": 4180.0,
        "stop_loss": 4170.0,
        "take_profit": 4200.0,
    }
    plan = build_setup_display_plan(invalid_setup, frozen_snapshot=valid_order_snapshot)
    assert plan["is_valid"] is True
    assert plan["entry"] == 4180.0
    assert plan["stop_loss"] == 4170.0
    assert plan["take_profit"] == 4200.0
