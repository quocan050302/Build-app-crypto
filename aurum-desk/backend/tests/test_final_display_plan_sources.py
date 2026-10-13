"""
backend/tests/test_final_display_plan_sources.py
V14.4.3 — Final Closure Suite for Canonical Plan Sources, Geometry & Financial Parity.

Tests:
1. PLANSRC01: Frozen LONG with actual_entry beyond TP (4202 > 4200) -> is_valid=False, INVALID_GEOMETRY.
2. PLANSRC02: Frozen SHORT with actual_entry beyond TP (4158 < 4160) -> is_valid=False, INVALID_GEOMETRY.
3. PLANSRC03: Valid frozen SHORT -> is_valid=True, signed RR without abs.
4. PLANSRC04: Incomplete frozen snapshot (missing SL) -> is_valid=False, INCOMPLETE_SOURCE (no hybrid piecing with setup).
5. PLANSRC05: Setup raw invalid does NOT break a valid bound frozen order.
"""
import pytest
from services.setup_display_plan_service import (
    build_setup_display_plan,
    select_authoritative_plan_source,
    validate_selected_plan,
)


def test_plansrc01_frozen_long_with_invalid_entry_beyond_tp_is_invalid():
    """PLANSRC01 (PHẦN 030, 154):
    Setup provisional levels: entry=4180, SL=4175, TP=4200.
    Order frozen snapshot: actual_entry=4202, SL=4175, TP=4200 (entry beyond TP).
    build_setup_display_plan MUST return is_valid=False, reason_code="INVALID_GEOMETRY".
    """
    setup = {
        "id": "setup-p01",
        "direction": "LONG",
        "state": "PAPER_OPEN",
        "provisional_entry": 4180.0,
        "provisional_sl": 4175.0,
        "provisional_tp": 4200.0,
        "updated_at": 1728000000000,
    }
    frozen_snap = {
        "source_kind": "FROZEN_ORDER",
        "direction": "LONG",
        "actual_entry": 4202.0,
        "planned_entry": 4180.0,
        "entry": 4202.0,
        "stop_loss": 4175.0,
        "take_profit": 4200.0,
        "financial": {"net_rr": 3.5, "gross_rr": 4.0},
    }

    plan = build_setup_display_plan(
        setup,
        pricing_mode="FROZEN_ORDER",
        frozen_snapshot=frozen_snap,
    )

    assert plan["is_valid"] is False, f"Expected is_valid=False for entry 4202 > TP 4200, got {plan}"
    assert plan["reason_code"] == "INVALID_GEOMETRY"
    assert plan["financial"] is None


def test_plansrc02_frozen_short_with_invalid_entry_beyond_tp_is_invalid():
    """PLANSRC02 (PHẦN 031, 154):
    Setup provisional levels: entry=4180, SL=4190, TP=4160.
    Order frozen snapshot: actual_entry=4158, SL=4190, TP=4160 (entry beyond TP for SHORT).
    build_setup_display_plan MUST return is_valid=False, reason_code="INVALID_GEOMETRY".
    """
    setup = {
        "id": "setup-p02",
        "direction": "SHORT",
        "state": "PAPER_OPEN",
        "provisional_entry": 4180.0,
        "provisional_sl": 4190.0,
        "provisional_tp": 4160.0,
        "updated_at": 1728000000000,
    }
    frozen_snap = {
        "source_kind": "FROZEN_ORDER",
        "direction": "SHORT",
        "actual_entry": 4158.0,
        "planned_entry": 4180.0,
        "entry": 4158.0,
        "stop_loss": 4190.0,
        "take_profit": 4160.0,
        "financial": {"net_rr": 1.5, "gross_rr": 2.0},
    }

    plan = build_setup_display_plan(
        setup,
        pricing_mode="FROZEN_ORDER",
        frozen_snapshot=frozen_snap,
    )

    assert plan["is_valid"] is False
    assert plan["reason_code"] == "INVALID_GEOMETRY"
    assert plan["financial"] is None


def test_plansrc03_valid_frozen_short_signed_rr_without_abs():
    """PLANSRC03 (PHẦN 031, 040):
    Valid SHORT order: entry=4180, SL=4190, TP=4160.
    Must return is_valid=True with correct signed geometry.
    """
    setup = {
        "id": "setup-p03",
        "direction": "SHORT",
        "state": "PAPER_OPEN",
        "provisional_entry": 4180.0,
        "provisional_sl": 4190.0,
        "provisional_tp": 4160.0,
        "updated_at": 1728000000000,
    }
    frozen_snap = {
        "source_kind": "FROZEN_ORDER",
        "direction": "SHORT",
        "actual_entry": 4180.0,
        "planned_entry": 4180.0,
        "entry": 4180.0,
        "stop_loss": 4190.0,
        "take_profit": 4160.0,
        "financial": {
            "gross_rr": 2.0,
            "net_rr": 1.8,
            "net_risk": 11.0,
            "net_reward": 19.8,
            "quantity": 0.1,
        },
    }

    plan = build_setup_display_plan(
        setup,
        pricing_mode="FROZEN_ORDER",
        frozen_snapshot=frozen_snap,
    )

    assert plan["is_valid"] is True
    assert plan["entry"] == 4180.0
    assert plan["stop_loss"] == 4190.0
    assert plan["take_profit"] == 4160.0
    assert plan["financial"] is not None
    assert plan["financial"]["gross_rr"] == 2.0


def test_plansrc04_incomplete_frozen_snapshot_does_not_piece_together_hybrid_triple():
    """PLANSRC04 (PHẦN 035):
    Frozen snapshot has entry=4182 and TP=4200, but SL is None.
    Setup has provisional_sl=4175.
    build_setup_display_plan MUST return is_valid=False, reason_code="INCOMPLETE_SOURCE".
    It must NOT borrow setup provisional SL to make a hybrid triple!
    """
    setup = {
        "id": "setup-p04",
        "direction": "LONG",
        "state": "PAPER_OPEN",
        "provisional_entry": 4180.0,
        "provisional_sl": 4175.0,
        "provisional_tp": 4200.0,
        "updated_at": 1728000000000,
    }
    frozen_snap = {
        "source_kind": "FROZEN_ORDER",
        "direction": "LONG",
        "actual_entry": 4182.0,
        "entry": 4182.0,
        "stop_loss": None,  # Missing!
        "take_profit": 4200.0,
    }

    plan = build_setup_display_plan(
        setup,
        pricing_mode="FROZEN_ORDER",
        frozen_snapshot=frozen_snap,
    )

    assert plan["is_valid"] is False
    assert plan["reason_code"] == "INCOMPLETE_SOURCE"
    assert plan["financial"] is None


def test_plansrc05_setup_raw_invalid_does_not_break_valid_bound_order():
    """PLANSRC05 (PHẦN 036):
    Setup has missing or invalid provisional levels (e.g. provisional_entry=None).
    Bound order has a complete and valid frozen snapshot.
    build_setup_display_plan MUST return is_valid=True with order's authoritative levels!
    """
    setup = {
        "id": "setup-p05",
        "direction": "LONG",
        "state": "PAPER_OPEN",
        "provisional_entry": None,  # Raw setup is invalid!
        "provisional_sl": None,
        "provisional_tp": None,
        "updated_at": 1728000000000,
    }
    frozen_snap = {
        "source_kind": "FROZEN_ORDER",
        "direction": "LONG",
        "actual_entry": 4182.0,
        "planned_entry": 4180.0,
        "entry": 4182.0,
        "stop_loss": 4175.0,
        "take_profit": 4200.0,
        "financial": {
            "gross_rr": 2.57,
            "net_rr": 2.3,
            "quantity": 0.1,
        },
    }

    plan = build_setup_display_plan(
        setup,
        pricing_mode="FROZEN_ORDER",
        frozen_snapshot=frozen_snap,
    )

    assert plan["is_valid"] is True, f"Expected order view to be valid despite invalid raw setup, got {plan}"
    assert plan["entry"] == 4182.0
    assert plan["actual_entry"] == 4182.0
    assert plan["stop_loss"] == 4175.0
    assert plan["take_profit"] == 4200.0
    assert plan["financial"] is not None
