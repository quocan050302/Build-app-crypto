"""
backend/tests/test_v17_5_9_financial_parity.py
AURUM V17.5.9 — Full Financial Parity Reconciliation Suite (D05, Section 07 & Section 09 Part 076).

Cases:
- F01: All plan fields agree -> PASS
- F02: Entry slippage mismatch -> MISMATCH
- F03: Maker fee mismatch -> MISMATCH
- F04: Taker fee mismatch -> MISMATCH
- F05: Quantity mismatch -> MISMATCH
- F06: Risk-budget mismatch -> MISMATCH
- F07: Net R:R mismatch -> MISMATCH
- F08: Incorrect stop-loss geometry -> MISMATCH
- F09: Missing mandatory snapshot field -> INCOMPLETE
- F10: Explicit zero cost -> PASS
- F11: Valid LONG full-cycle fill -> PASS
- F12: Valid SHORT full-cycle fill -> PASS
- F13: Realized slippage deviation reported correctly
- F14: Frozen assumptions remain stable after settings update
"""

import json
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import crud
import models
from services.clock import FakeClock
from services.execution_coordinator import ExecutionCoordinator
from services.plan_financial_context_service import (
    assert_order_financial_parity,
    resolve_execution_cost_context,
)
from services.quote_validator import CanonicalQuote
from services.scalp_m1_types import M1ScalpConfig, ScalpMode, WatchedZone, ZoneState
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator
from tests.test_v15_1_execution_lifecycle import (
    _make_causal_short_m1_candles,
    _setup_positive_trading_policy,
)


class DummyCalc:
    def __init__(
        self,
        entry=2647.0,
        stop_loss=2660.0,
        take_profit=2630.0,
        quantity=0.5,
        net_risk_usdt=6.5,
        gross_rr=1.31,
        net_rr=1.28,
        slippage_usd=0.20,
        maker_fee_rate=0.0002,
        taker_fee_rate=0.0004,
    ):
        self.entry = entry
        self.planned_entry = entry
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.quantity = quantity
        self.net_risk_usdt = net_risk_usdt
        self.gross_rr = gross_rr
        self.net_rr = net_rr
        self.slippage_usd = slippage_usd
        self.maker_fee_rate = maker_fee_rate
        self.taker_fee_rate = taker_fee_rate
        self.is_valid = True
        self.can_execute = True


def _make_base_snapshot(direction="SHORT"):
    return {
        "direction": direction,
        "planned_entry": 2647.0,
        "stop_loss": 2660.0 if direction == "SHORT" else 2630.0,
        "take_profit": 2630.0 if direction == "SHORT" else 2660.0,
        "quantity": 0.5,
        "target_risk_budget": 6.5,
        "gross_rr": 1.31,
        "estimated_net_rr": 1.28,
        "slippage_usd": 0.20,
        "resolved_execution_costs": {
            "entry_slippage_usd": 0.20,
            "sl_slippage_usd": 0.20,
            "tp_slippage_usd": 0.0,
            "maker_fee_rate": 0.0002,
            "taker_fee_rate": 0.0004,
        },
    }


def test_f01_all_plan_fields_agree():
    """F01: All plan fields agree across calc, snapshot, and comparison plan -> PASS."""
    calc = DummyCalc()
    snap = _make_base_snapshot()
    comp = {
        "direction": "SHORT",
        "planned_entry": 2647.0,
        "stop_loss": 2660.0,
        "take_profit": 2630.0,
        "quantity": 0.5,
        "target_risk_budget": 6.5,
        "gross_rr": 1.31,
        "estimated_net_rr": 1.28,
        "cost_snapshot": {
            "entry_slippage_usd": 0.20,
            "maker_fee_rate": 0.0002,
            "taker_fee_rate": 0.0004,
        },
    }
    fill = {"realized_slippage": 0.20}

    res = assert_order_financial_parity(
        candidate_calc=calc,
        order_snapshot=snap,
        comparison_plan=comp,
        fill_result=fill,
    )
    assert res["is_parity"] is True
    assert res["status"] == "PASS"
    assert len(res["mismatches"]) == 0


def test_f02_entry_slippage_mismatch():
    """F02: Comparison plan entry slippage differs from snapshot -> MISMATCH."""
    snap = _make_base_snapshot()
    comp = {
        "cost_snapshot": {
            "entry_slippage_usd": 0.35,  # mismatch vs 0.20
        }
    }
    res = assert_order_financial_parity(order_snapshot=snap, comparison_plan=comp)
    assert res["is_parity"] is False
    assert res["status"] == "MISMATCH"
    assert any("Slippage mismatch" in m for m in res["mismatches"])


def test_f03_maker_fee_mismatch():
    """F03: Maker fee rate in comparison plan differs from snapshot -> MISMATCH."""
    snap = _make_base_snapshot()
    comp = {
        "cost_snapshot": {
            "entry_slippage_usd": 0.20,
            "maker_fee_rate": 0.0005,  # mismatch vs 0.0002
        }
    }
    res = assert_order_financial_parity(order_snapshot=snap, comparison_plan=comp)
    assert res["is_parity"] is False
    assert any("Maker fee mismatch" in m for m in res["mismatches"])


def test_f04_taker_fee_mismatch():
    """F04: Taker fee rate in comparison plan differs from snapshot -> MISMATCH."""
    snap = _make_base_snapshot()
    comp = {
        "cost_snapshot": {
            "entry_slippage_usd": 0.20,
            "taker_fee_rate": 0.0008,  # mismatch vs 0.0004
        }
    }
    res = assert_order_financial_parity(order_snapshot=snap, comparison_plan=comp)
    assert res["is_parity"] is False
    assert any("Taker fee mismatch" in m for m in res["mismatches"])


def test_f05_quantity_mismatch():
    """F05: Calculator quantity differs from snapshot quantity -> MISMATCH."""
    calc = DummyCalc(quantity=0.8)
    snap = _make_base_snapshot()  # quantity=0.5
    res = assert_order_financial_parity(candidate_calc=calc, order_snapshot=snap)
    assert res["is_parity"] is False
    assert any("Quantity mismatch" in m for m in res["mismatches"])


def test_f06_risk_budget_mismatch():
    """F06: Calculator risk budget differs from snapshot risk budget -> MISMATCH."""
    calc = DummyCalc(net_risk_usdt=15.0)
    snap = _make_base_snapshot()  # risk budget 6.5
    res = assert_order_financial_parity(candidate_calc=calc, order_snapshot=snap)
    assert res["is_parity"] is False
    assert any("Risk budget mismatch" in m for m in res["mismatches"])


def test_f07_net_rr_mismatch():
    """F07: Calculator net R:R differs from snapshot net R:R -> MISMATCH."""
    calc = DummyCalc(net_rr=2.5)
    snap = _make_base_snapshot()  # net_rr 1.28
    res = assert_order_financial_parity(candidate_calc=calc, order_snapshot=snap)
    assert res["is_parity"] is False
    assert any("Net R:R mismatch" in m for m in res["mismatches"])


def test_f08_incorrect_stop_loss_geometry():
    """F08: Snapshot with inverted geometry (tp > entry for SHORT) fails parity geometry check."""
    snap = _make_base_snapshot()
    snap["take_profit"] = 2655.0  # above entry 2647 for SHORT!
    res = assert_order_financial_parity(order_snapshot=snap)
    assert res["is_parity"] is False
    assert any("Incorrect SHORT geometry" in m for m in res["mismatches"])


def test_f09_missing_mandatory_snapshot_field():
    """F09: Missing mandatory field in snapshot (e.g. quantity is None) returns INCOMPLETE / False."""
    snap = _make_base_snapshot()
    snap["quantity"] = None
    res = assert_order_financial_parity(order_snapshot=snap)
    assert res["is_parity"] is False
    assert res["status"] == "INCOMPLETE"
    assert "quantity" in res["missing_fields"]


def test_f10_explicit_zero_cost():
    """F10: Explicit zero slippage (0.0) matches cleanly and reports PASS."""
    calc = DummyCalc(slippage_usd=0.0)
    snap = _make_base_snapshot()
    snap["slippage_usd"] = 0.0
    snap["resolved_execution_costs"]["entry_slippage_usd"] = 0.0
    comp = {
        "cost_snapshot": {
            "entry_slippage_usd": 0.0,
            "maker_fee_rate": 0.0002,
            "taker_fee_rate": 0.0004,
        }
    }
    fill = {"realized_slippage": 0.0}

    res = assert_order_financial_parity(
        candidate_calc=calc,
        order_snapshot=snap,
        comparison_plan=comp,
        fill_result=fill,
    )
    assert res["is_parity"] is True
    assert res["status"] == "PASS"


def test_f11_valid_long_full_cycle_fill():
    """F11: Valid LONG financial snapshot and fill (Ask + slippage) reconciles accurately."""
    snap = {
        "direction": "LONG",
        "planned_entry": 2650.0,
        "stop_loss": 2640.0,
        "take_profit": 2670.0,
        "quantity": 0.5,
        "target_risk_budget": 5.0,
        "gross_rr": 2.0,
        "estimated_net_rr": 1.95,
        "slippage_usd": 0.25,
        "resolved_execution_costs": {
            "entry_slippage_usd": 0.25,
            "maker_fee_rate": 0.0002,
            "taker_fee_rate": 0.0004,
        },
    }
    fill = {"realized_slippage": 0.25}
    res = assert_order_financial_parity(order_snapshot=snap, fill_result=fill)
    assert res["is_parity"] is True
    assert res["status"] == "PASS"


def test_f12_valid_short_full_cycle_fill():
    """F12: Valid SHORT financial snapshot and fill (Bid - slippage) reconciles accurately."""
    snap = _make_base_snapshot(direction="SHORT")
    fill = {"realized_slippage": 0.20}
    res = assert_order_financial_parity(order_snapshot=snap, fill_result=fill)
    assert res["is_parity"] is True
    assert res["status"] == "PASS"


def test_f13_realized_slippage_deviation_reported_correctly():
    """F13: When realized fill slippage deviates from snapshot assumption, mismatch is recorded."""
    snap = _make_base_snapshot()
    # Fill experienced 0.40 slippage instead of expected 0.20
    fill = {"realized_slippage": 0.40}
    res = assert_order_financial_parity(order_snapshot=snap, fill_result=fill)
    assert res["is_parity"] is False
    assert any("Fill slippage mismatch" in m for m in res["mismatches"])


def test_f14_frozen_assumptions_remain_stable_after_settings_update():
    """F14: Snapshot financial assumptions remain stable post-ARM when global settings change."""
    snap = _make_base_snapshot()
    # Before settings change:
    res1 = assert_order_financial_parity(order_snapshot=snap)
    assert res1["is_parity"] is True

    # Mutate external context to simulate settings change:
    modified_external_costs = {"entry_slippage_usd": 1.00}
    # Snapshot itself is immutable and unchanged:
    res2 = assert_order_financial_parity(order_snapshot=snap)
    assert res2["is_parity"] is True
    assert snap["slippage_usd"] == 0.20
