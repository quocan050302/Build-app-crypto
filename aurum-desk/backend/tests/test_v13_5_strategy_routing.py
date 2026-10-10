"""
AURUM DESK — V13.5 STRATEGY ROUTING & DIAGNOSTICS TESTS
Verifies:
- Routing isolation between CURRENT_BASELINE and NY_ADAPTIVE.
- diagnose_run_difference explanation of confirmed setup variations.
"""

import pytest
from lab.replay_metrics import diagnose_run_difference


def test_01_diagnose_run_difference_variant_explanation():
    before_res = {
        "strategy_variant": "CURRENT_BASELINE",
        "trade_type_breakdown": {"SMC_CONFIRMED": 1, "SMC_CONTEXT_SCHEDULED_PAPER": 63},
        "fills_count": 64
    }
    after_res = {
        "strategy_variant": "NY_ADAPTIVE",
        "trade_type_breakdown": {"SMC_CONFIRMED": 17, "SMC_CONTEXT_SCHEDULED_PAPER": 50},
        "fills_count": 67
    }

    diag = diagnose_run_difference(before_res, after_res)
    assert diag["has_variant_mismatch"] is True
    assert diag["confirmed_before"] == 1
    assert diag["confirmed_after"] == 17
    assert any("ROUTING_EXPLANATION" in d for d in diag["differences"])
