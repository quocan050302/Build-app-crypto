"""
backend/tests/test_v17_5_5_cost_authority.py
AURUM V17.5.5 — Unified Execution Cost Authority & Order Parity Test Suite.
Covers Parts 041–070, Appendix B, E1:
1. Unified default fee authority: maker 0.0002, taker 0.0004 (no 0.0006 discrepancy).
2. Explicit 0.0 slippage preserved, distinct from missing cost snapshot.
3. Custom slippage overrides (e.g. 0.27) resolved deterministically.
4. Golden test hand-checks for MARKET fills under various slippages.
5. Research comparison snapshot and production PaperOrder snapshot parity.
6. Settings immutability: changing current settings does not alter frozen historical snapshots.
"""

import math
import pytest
from services.plan_financial_context_service import resolve_execution_cost_context
from services.stop_placement_research_service import (
    evaluate_comparison_entry,
    read_verified_comparison_cost_snapshot,
    resolve_stop_comparison_execution_context,
)


def test_unified_default_fee_rates():
    """Phần 053, Appendix B1: Unified default fee rates must be maker 0.0002 and taker 0.0004."""
    res = resolve_execution_cost_context(
        candidate=None,
        settings={"config_version": 1},
        config=None,
        as_of_ms=1000,
    )
    assert res["available"] is True
    assert res["maker_fee_rate"] == 0.0002
    assert res["taker_fee_rate"] == 0.0004  # Not 0.0006!
    assert res["entry_slippage_usd"] == 0.10


def test_explicit_zero_slippage_preserved():
    """Phần 043, Appendix B5: Explicit 0.0 slippage is preserved, not defaulted to 0.10."""
    snap = {
        "entry_slippage_usd": 0.0,
        "tp_slippage_usd": 0.0,
        "sl_slippage_usd": 0.0,
        "maker_fee_rate": 0.0002,
        "taker_fee_rate": 0.0004,
        "version": 1,
    }
    res = resolve_execution_cost_context(snapshot=snap)
    assert res["available"] is True
    assert res["entry_slippage_usd"] == 0.0
    assert res["tp_slippage_usd"] == 0.0
    assert res["sl_slippage_usd"] == 0.0


def test_custom_slippage_override():
    """Phần 059, Appendix B5: Custom slippage override (0.27) resolves correctly."""
    snap = {
        "entry_slippage_usd": 0.27,
        "tp_slippage_usd": 0.0,
        "sl_slippage_usd": 0.27,
        "maker_fee_rate": 0.0001,
        "taker_fee_rate": 0.0003,
        "version": 2,
    }
    res = resolve_execution_cost_context(snapshot=snap)
    assert res["available"] is True
    assert res["entry_slippage_usd"] == 0.27
    assert res["maker_fee_rate"] == 0.0001
    assert res["taker_fee_rate"] == 0.0003
    assert res["version"] == 2


def test_golden_fill_hand_check():
    """
    Appendix B5: Golden hand-check fill calculation:
    Bid 2000.00, Ask 2000.20
    - entry_slippage 0.10 -> MARKET LONG 2000.30, MARKET SHORT 1999.90
    - entry_slippage 0.27 -> MARKET LONG 2000.47, MARKET SHORT 1999.73
    - explicit 0.0 -> MARKET LONG 2000.20, MARKET SHORT 2000.00
    """
    bid = 2000.00
    ask = 2000.20

    # 1. Standard 0.10 slippage
    trig_l, supp_l, fill_l, _ = evaluate_comparison_entry(
        direction="LONG", planned_entry=2000.00, order_type="MARKET", bid=bid, ask=ask, order_slip=0.10
    )
    trig_s, supp_s, fill_s, _ = evaluate_comparison_entry(
        direction="SHORT", planned_entry=2000.00, order_type="MARKET", bid=bid, ask=ask, order_slip=0.10
    )
    assert trig_l and supp_l and abs(fill_l - 2000.30) < 1e-4
    assert trig_s and supp_s and abs(fill_s - 1999.90) < 1e-4

    # 2. Override 0.27 slippage
    trig_l27, _, fill_l27, _ = evaluate_comparison_entry(
        direction="LONG", planned_entry=2000.00, order_type="MARKET", bid=bid, ask=ask, order_slip=0.27
    )
    trig_s27, _, fill_s27, _ = evaluate_comparison_entry(
        direction="SHORT", planned_entry=2000.00, order_type="MARKET", bid=bid, ask=ask, order_slip=0.27
    )
    assert abs(fill_l27 - 2000.47) < 1e-4
    assert abs(fill_s27 - 1999.73) < 1e-4

    # 3. Explicit 0.0 slippage
    trig_l0, _, fill_l0, _ = evaluate_comparison_entry(
        direction="LONG", planned_entry=2000.00, order_type="MARKET", bid=bid, ask=ask, order_slip=0.0
    )
    trig_s0, _, fill_s0, _ = evaluate_comparison_entry(
        direction="SHORT", planned_entry=2000.00, order_type="MARKET", bid=bid, ask=ask, order_slip=0.0
    )
    assert abs(fill_l0 - 2000.20) < 1e-4
    assert abs(fill_s0 - 2000.00) < 1e-4


def test_research_comparison_and_order_parity():
    """Phần 060, Appendix B6: Research cost snapshot and order cost snapshot have identical values and versions."""
    class MockCandidate:
        model_id = "M1_CONTINUATION"
        candidate_key = "test_cand_parity"
        as_of_ms = 10000
        direction = "LONG"
        planned_entry = 2650.0

    cand = MockCandidate()
    settings = {
        "slippage_usd": 0.15,
        "config_version": 3,
    }

    # Research execution context
    res_ctx = resolve_stop_comparison_execution_context(
        cand=cand,
        policy_settings=settings,
        decision_as_of_ms=10000,
    )
    res_costs = res_ctx["cost_snapshot"]

    # Production order resolver
    order_costs = resolve_execution_cost_context(
        candidate=cand,
        settings=settings,
        as_of_ms=10000,
    )

    assert res_costs["entry_slippage_usd"] == order_costs["entry_slippage_usd"] == 0.15
    assert res_costs["maker_fee_rate"] == order_costs["maker_fee_rate"] == 0.0002
    assert res_costs["taker_fee_rate"] == order_costs["taker_fee_rate"] == 0.0004
    assert res_costs["version"] == order_costs["version"] == 3


def test_snapshot_immutability_against_settings_change():
    """Phần 057, 083: Frozen historical snapshot is NOT overwritten when current settings change."""
    historical_snap = {
        "entry_slippage_usd": 0.08,
        "tp_slippage_usd": 0.0,
        "sl_slippage_usd": 0.08,
        "maker_fee_rate": 0.0002,
        "taker_fee_rate": 0.0004,
        "version": 1,
    }
    plan = {
        "cost_snapshot": historical_snap,
    }

    # Read historical plan
    costs1 = read_verified_comparison_cost_snapshot(plan)
    assert costs1["entry_slippage_usd"] == 0.08
    assert costs1["version"] == 1

    # Simulate settings updated to higher slippage 0.25
    new_settings = {"slippage_usd": 0.25, "config_version": 2}
    costs_from_settings = resolve_execution_cost_context(settings=new_settings)
    assert costs_from_settings["entry_slippage_usd"] == 0.25
    assert costs_from_settings["version"] == 2

    # Historical plan remains strictly intact at 0.08
    costs2 = read_verified_comparison_cost_snapshot(plan)
    assert costs2["entry_slippage_usd"] == 0.08
    assert costs2["version"] == 1
