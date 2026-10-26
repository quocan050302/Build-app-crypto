"""
backend/tests/test_v14_4_13_drawdown.py
V14.4.13 — Correct Point-in-Time Realized Maximum Drawdown Test Suite.

Covers:
  - D01: Drawdown Repro & Invariants (PHẦN 004, 046–051, 067)
        - Repro 1: 1000 -> 900 -> 2000 => Max DD USD: 100.0, Max DD %: 10.0%
        - Repro 2: 1000 -> 800 -> 2000 -> 1700 => Max DD USD: 300.0, Max DD %: 20.0%
        - Independent tracking of USD and % maxima with peak/trough references
        - Edge cases: empty, only wins, only losses, recovery, negative equity, invalid capital,
          same timestamp tie-breaking.
"""
from __future__ import annotations

import math
import pytest
from services.forward_paper_service import compute_realized_drawdown


# =========================================================================
# D01: MANDATORY REPRO EXAMPLES (PHẦN 004, 051, 067)
# =========================================================================

def test_dd01_drawdown_repro_example_one():
    """PHẦN 004, 051, 067 (D01 - Example 1):
    1000 -> 900 -> 2000
    Start: 1000
    Trade 1: -100 (equity 900, peak 1000) => DD USD: 100, DD %: 10.0%
    Trade 2: +1100 (equity 2000, peak 2000) => DD USD: 0, DD %: 0.0%
    Expected:
      Max DD USD: 100.0
      Max DD %: 10.0% (old buggy formula divided by final peak 2000 = 5.0%)
    """
    capital = 1000.0
    cashflows = [
        {"order_id": "ord_1", "net_pnl": -100.0, "closed_at": 1000},
        {"order_id": "ord_2", "net_pnl": 1100.0, "closed_at": 2000},
    ]

    res = compute_realized_drawdown(capital, cashflows)
    assert res["status"] == "OK"
    assert res["max_drawdown_usd"] == 100.0
    assert res["max_drawdown_pct"] == 10.0
    assert res["peak_usd_ref"] == 1000.0
    assert res["trough_usd_ref"] == 900.0
    assert res["peak_pct_ref"] == 1000.0
    assert res["trough_pct_ref"] == 900.0


def test_dd01_drawdown_repro_example_two():
    """PHẦN 004, 051, 067 (D01 - Example 2):
    1000 -> 800 -> 2000 -> 1700
    Start: 1000
    Trade 1: -200 (equity 800, peak 1000) => DD USD: 200, DD %: 20.0%
    Trade 2: +1200 (equity 2000, peak 2000) => DD USD: 0, DD %: 0.0%
    Trade 3: -300 (equity 1700, peak 2000) => DD USD: 300, DD %: 15.0%
    Expected:
      Max DD USD: 300.0 (occurred at trade 3)
      Max DD %: 20.0% (occurred at trade 1)
      Proves USD and % maxima occur at different points and must be tracked independently!
    """
    capital = 1000.0
    cashflows = [
        {"order_id": "ord_1", "net_pnl": -200.0, "closed_at": 1000},
        {"order_id": "ord_2", "net_pnl": 1200.0, "closed_at": 2000},
        {"order_id": "ord_3", "net_pnl": -300.0, "closed_at": 3000},
    ]

    res = compute_realized_drawdown(capital, cashflows)
    assert res["status"] == "OK"
    assert res["max_drawdown_usd"] == 300.0
    assert res["max_drawdown_pct"] == 20.0
    # Peak and trough references for USD maximum
    assert res["peak_usd_ref"] == 2000.0
    assert res["trough_usd_ref"] == 1700.0
    # Peak and trough references for PCT maximum
    assert res["peak_pct_ref"] == 1000.0
    assert res["trough_pct_ref"] == 800.0


# =========================================================================
# D01: EDGE CASES (PHẦN 050)
# =========================================================================

def test_d01_drawdown_empty_cashflows():
    """PHẦN 050: Empty trade list returns NO_TRADES with 0 drawdown."""
    res = compute_realized_drawdown(1000.0, [])
    assert res["status"] == "NO_TRADES"
    assert res["max_drawdown_usd"] == 0.0
    assert res["max_drawdown_pct"] == 0.0


def test_d01_drawdown_only_wins():
    """PHẦN 050: Monotonically increasing equity curve has zero drawdown."""
    capital = 1000.0
    cashflows = [
        {"order_id": "ord_1", "net_pnl": 100.0, "closed_at": 1000},
        {"order_id": "ord_2", "net_pnl": 200.0, "closed_at": 2000},
        {"order_id": "ord_3", "net_pnl": 300.0, "closed_at": 3000},
    ]
    res = compute_realized_drawdown(capital, cashflows)
    assert res["status"] == "OK"
    assert res["max_drawdown_usd"] == 0.0
    assert res["max_drawdown_pct"] == 0.0


def test_d01_drawdown_only_losses():
    """PHẦN 050: Monotonically decreasing equity curve."""
    capital = 1000.0
    cashflows = [
        {"order_id": "ord_1", "net_pnl": -100.0, "closed_at": 1000},
        {"order_id": "ord_2", "net_pnl": -200.0, "closed_at": 2000},
        {"order_id": "ord_3", "net_pnl": -300.0, "closed_at": 3000},
    ]
    res = compute_realized_drawdown(capital, cashflows)
    assert res["status"] == "OK"
    assert res["max_drawdown_usd"] == 600.0
    assert res["max_drawdown_pct"] == 60.0


def test_d01_drawdown_recovery_to_new_peak():
    """PHẦN 050: Dip, recovery to new ATH, second smaller dip."""
    capital = 1000.0
    cashflows = [
        {"order_id": "ord_1", "net_pnl": -500.0, "closed_at": 1000},  # eq 500, dd 500 (50%)
        {"order_id": "ord_2", "net_pnl": 1000.0, "closed_at": 2000}, # eq 1500, peak 1500
        {"order_id": "ord_3", "net_pnl": -300.0, "closed_at": 3000},  # eq 1200, dd 300 (20%)
    ]
    res = compute_realized_drawdown(capital, cashflows)
    assert res["status"] == "OK"
    assert res["max_drawdown_usd"] == 500.0
    assert res["max_drawdown_pct"] == 50.0


def test_d01_drawdown_negative_equity():
    """PHẦN 050: Losses exceed capital, equity becomes negative.
    Percentage must NOT be falsely clamped to 100% to conceal catastrophic loss.
    """
    capital = 1000.0
    cashflows = [
        {"order_id": "ord_1", "net_pnl": -1500.0, "closed_at": 1000},  # eq -500
    ]
    res = compute_realized_drawdown(capital, cashflows)
    assert res["status"] == "OK"
    assert res["max_drawdown_usd"] == 1500.0
    assert res["max_drawdown_pct"] == 150.0  # Not clamped!


def test_d01_drawdown_invalid_capital():
    """PHẦN 050: Non-positive or NaN starting capital returns DATA_INVALID."""
    res_zero = compute_realized_drawdown(0.0, [{"order_id": "ord_1", "net_pnl": -10.0}])
    assert res_zero["status"] == "DATA_INVALID"
    assert res_zero["max_drawdown_usd"] == 0.0

    res_neg = compute_realized_drawdown(-500.0, [{"order_id": "ord_1", "net_pnl": -10.0}])
    assert res_neg["status"] == "DATA_INVALID"

    res_nan = compute_realized_drawdown(float("nan"), [{"order_id": "ord_1", "net_pnl": -10.0}])
    assert res_nan["status"] == "DATA_INVALID"


def test_d01_drawdown_same_timestamp_deterministic():
    """PHẦN 050: Stable tie-breaking on identical timestamps by order_id."""
    capital = 1000.0
    cashflows = [
        {"order_id": "ord_B", "net_pnl": 500.0, "closed_at": 1000},
        {"order_id": "ord_A", "net_pnl": -200.0, "closed_at": 1000},
    ]
    # ord_A (-200) comes before ord_B (+500)
    # Start: 1000 -> 800 (dd 200, 20%) -> 1300
    res = compute_realized_drawdown(capital, cashflows)
    assert res["status"] == "OK"
    assert res["max_drawdown_usd"] == 200.0
    assert res["max_drawdown_pct"] == 20.0
