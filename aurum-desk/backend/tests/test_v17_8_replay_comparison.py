"""
backend/tests/test_v17_8_replay_comparison.py
AURUM V17.8 Phase 8 Tests:
Deterministic Historical Replay and Quantitative Matched-Comparison Harness.
Compares Variants A, B, C, D on identical inputs with risk conservation.
"""

import pytest
from services.unicorn_replay_service import (
    UnicornReplayHarness,
    ReplayOpportunityInput,
)
from domain_calculator import CostAssumptions


def _make_sample_opportunity(
    opp_id: str = "opp_test_01",
    direction: str = "SHORT",
    entry: float = 4021.08,
    legacy_sl: float = 4021.67,
    structural_anchor: float = 4024.50,
    unicorn_invalidation: float = 4024.50,
    tp: float = 4002.00,
    atr_m1: float = 0.98,
    has_unicorn: bool = True,
    unicorn_valid: bool = True,
    bars: list = None,
) -> ReplayOpportunityInput:
    if bars is None:
        # Default scenario:
        # Bar 1 touches 4021.74 (stopping out tight Variant A at 4021.67, but well below Variant C at 4024.80)
        # Subsequent bars fall to 4001.0 (hitting TP at 4002.0)
        bars = [
            {"timestamp": 1000, "open": 4021.08, "high": 4021.75, "low": 4020.50, "close": 4020.80},
            {"timestamp": 61000, "open": 4020.80, "high": 4021.00, "low": 4015.00, "close": 4016.00},
            {"timestamp": 121000, "open": 4016.00, "high": 4016.50, "low": 4008.00, "close": 4009.00},
            {"timestamp": 181000, "open": 4009.00, "high": 4010.00, "low": 4001.00, "close": 4001.50},
        ]

    return ReplayOpportunityInput(
        opportunity_id=opp_id,
        symbol="XAUUSDT",
        direction=direction,
        entry_price=entry,
        take_profit=tp,
        decision_ms=1000,
        legacy_sl=legacy_sl,
        structural_anchor_price=structural_anchor,
        unicorn_invalidation_price=unicorn_invalidation,
        atr_m1=atr_m1,
        spread=0.20,
        forward_bars=bars,
        has_unicorn_evidence=has_unicorn,
        unicorn_valid=unicorn_valid,
    )


def test_variant_a_tight_stop_early_stop_out_vs_variant_c_survival():
    """
    Simulates the exact historical scenario:
    - Variant A (legacy FVG edge + 5 ticks) placed at 4021.67.
    - Market Ask touches 4021.75 in first 30 seconds -> Variant A stopped out early.
    - Variant C uses Breaker distal invalidation (4024.50 + buffer) -> survives noise.
    - Price subsequently trends to 4001.00, hitting TP at 4002.00.
    """
    harness = UnicornReplayHarness(
        capital_usdt=1000.0,
        risk_pct=0.25,  # $2.50 USDT budget
        leverage=20,
        min_net_rr=1.5,
    )

    opp = _make_sample_opportunity()
    metrics = harness.run_matched_comparison([opp])

    mA = metrics["A"]
    mC = metrics["C"]

    # Variant A must have executed, stopped out in <= 30s, and post-stop recovered
    assert mA.executed_trades == 1
    assert mA.early_sl_30s == 1
    assert mA.loss_count == 1
    assert mA.post_stop_recovery_rate == 1.0  # Market reached TP after stopping out A

    # Variant C must have executed, survived the noise, and reached TP
    assert mC.executed_trades == 1
    assert mC.early_sl_30s == 0
    assert mC.win_count == 1
    assert mC.win_rate == 1.0
    assert mC.net_profit > 0


def test_matched_comparison_determinism():
    """
    Verifies that running the matched-comparison harness repeatedly with the same
    inputs produces identical, bit-for-bit results (zero nondeterminism).
    """
    harness = UnicornReplayHarness(capital_usdt=1000.0, risk_pct=0.25, leverage=20)
    opps = [
        _make_sample_opportunity(opp_id=f"opp_{i}", entry=4020.0 + i, tp=4000.0)
        for i in range(3)
    ]

    res1 = harness.run_matched_comparison(opps)
    res2 = harness.run_matched_comparison(opps)

    for vid in ("A", "B", "C", "D"):
        m1 = res1[vid].to_dict()
        m2 = res2[vid].to_dict()
        assert m1 == m2


def test_intrabar_ambiguity_conservative_sl_first():
    """
    When both SL and TP are touched in the exact same candle, the conservative rule
    must trigger SL first and flag the exit as ambiguous.
    """
    harness = UnicornReplayHarness(capital_usdt=1000.0, risk_pct=0.25, leverage=20)

    # Giant bar spanning both SL (4025.0) and TP (4000.0) from entry 4020.0
    collision_bars = [
        {"timestamp": 1000, "open": 4020.0, "high": 4030.0, "low": 3995.0, "close": 4005.0},
    ]

    opp = _make_sample_opportunity(
        entry=4020.0,
        legacy_sl=4025.0,
        tp=4000.0,
        bars=collision_bars,
    )

    metrics = harness.run_matched_comparison([opp])
    mA = metrics["A"]

    assert mA.executed_trades == 1
    assert mA.ambiguous_exits == 1
    assert mA.loss_count == 1  # Conservative SL-first invariant
    assert mA.win_count == 0


def test_unverifiable_data_rejected_or_flagged():
    """
    An opportunity with missing forward bars is flagged as UNVERIFIABLE.
    """
    harness = UnicornReplayHarness()
    opp_empty = _make_sample_opportunity(bars=[])

    metrics = harness.run_matched_comparison([opp_empty])
    for vid in ("A", "B", "C", "D"):
        assert metrics[vid].unverifiable_exits == 1
        assert metrics[vid].executed_trades == 0
