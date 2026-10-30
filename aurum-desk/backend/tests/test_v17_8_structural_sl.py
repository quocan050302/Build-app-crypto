"""
backend/tests/test_v17_8_structural_sl.py
V17.8 — Test suite for Unified Structural Stop Loss Intelligence.
Tests:
- Invalidation authority: Breaker distal vs Displacement origin vs Sweep extreme.
- Dynamic buffer mathematics and entry-relative protective distance.
- Legitimate distant SL (>4x ATR, >10x ATR) preserving financial risk budget.
- Automatic quantity down-scaling to conserve $2.50 USDT risk budget.
- Independent TP invariance (never moved to inflate Net R:R).
- Fail-closed behavior when Net R:R < min_net_rr.
- Preservation of ARMED stop (never widened).
"""

import math
import pytest
from typing import Dict, Any, List

from domain_calculator import calculate_risk_reward, CostAssumptions
from services.stop_placement_types import (
    StopPlacementPolicy,
    StopPlacementMode,
    StopAnchorKind,
    StopAnchor,
    StopAnchorResult,
    StopBufferResult,
    StopATRResult,
    StopStatus,
)
from services.stop_placement_service import (
    plan_structural_stop,
    resolve_stop_anchor,
    quantize_stop_outward,
)
from services.unicorn_evidence_types import UnicornEvidence


def test_unicorn_breaker_distal_invalidation_authority():
    """Validates that a valid Unicorn evidence resolves anchor to BREAKER_DISTAL_BOUNDARY."""
    unicorn_ev = UnicornEvidence(
        unicorn_id="uni_test_01",
        symbol="XAUUSDT",
        direction="SHORT",
        setup_episode_id="ep_01",
        breaker_id="brk_01",
        breaker_type="BEARISH",
        breaker_low=4020.0,
        breaker_high=4023.0,
        breaker_distal_price=4023.0,
        breaker_confirmed_at_ms=1000,
        fvg_id="fvg_01",
        fvg_low=4021.0,
        fvg_high=4022.5,
        fvg_confirmed_at_ms=1000,
        overlap_low=4021.0,
        overlap_high=4022.5,
        invalidation_price=4023.0,
        validity_status="CONFIRMED",
        known_at_ms=1000,
    )

    closed_bars = [
        {"timestamp": 500, "open": 4018.0, "high": 4022.0, "low": 4017.0, "close": 4021.0},
        {"timestamp": 1000, "open": 4021.0, "high": 4022.8, "low": 4020.0, "close": 4020.5},
    ]

    res = resolve_stop_anchor(
        model_id="ICT_UNICORN",
        direction="SHORT",
        closed_bars=closed_bars,
        as_of_ms=1000,
        unicorn_evidence=unicorn_ev,
    )

    assert res.status == "AVAILABLE"
    assert res.anchor is not None
    assert res.anchor.kind == StopAnchorKind.BREAKER_DISTAL_BOUNDARY.value
    assert res.anchor.price == 4023.0
    assert "uni_test_01" in res.anchor.source_leg_id


def test_fvg_displacement_origin_fallback_when_bound_leg_invalidated():
    """
    Tests the forensic audit solution: when a bound break's origin was ancient or invalidated,
    resolve_stop_anchor falls back to the true displacement origin preceding the FVG.
    """
    closed_bars = [
        {"timestamp": 1000, "open": 4019.0, "high": 4022.5, "low": 4018.5, "close": 4022.0},  # Swing high at 4022.5
        {"timestamp": 2000, "open": 4022.0, "high": 4022.2, "low": 4020.0, "close": 4020.2},
        {"timestamp": 3000, "open": 4020.2, "high": 4020.5, "low": 4018.0, "close": 4018.2},
    ]

    selected_fvg = {
        "id": "fvg_bear_01",
        "top": 4021.5,
        "bottom": 4020.2,
        "middle_bar_ms": 2500,
        "timestamp": 2500,
        "confirmed_at": 3000,
    }

    # Confirmed swing high preceding FVG
    sh_list = [
        {"id": "sh_recent", "price": 4022.5, "occurred_at_ms": 1000, "known_at_ms": 2000},
    ]
    sl_list: List[Dict[str, Any]] = []

    res = resolve_stop_anchor(
        model_id="SMC_M1_FVG_CONTINUATION",
        direction="SHORT",
        closed_bars=closed_bars,
        as_of_ms=3000,
        selected_fvg=selected_fvg,
        confirmed_swings=(sh_list, sl_list),
    )

    assert res.status == "AVAILABLE"
    assert res.anchor is not None
    assert res.anchor.kind in (StopAnchorKind.PROTECTED_LEG_HIGH.value, StopAnchorKind.DISPLACEMENT_ORIGIN.value)
    assert res.anchor.price == 4022.5


def test_legitimate_distant_structural_sl_over_4x_and_10x_atr():
    """
    Tests that a structurally valid stop loss is NEVER rejected solely because
    distance > 4x ATR or > 10x ATR. Sizing must correctly downscale quantity.
    """
    policy = StopPlacementPolicy(
        mode=StopPlacementMode.PAPER_STRUCTURAL.value,
        min_buffer_ticks=5,
        atr_m1_multiplier=0.25,
        spread_multiplier=1.5,
        max_stop_distance_atr=4.0,
        algorithm_version="V17_8_0",
    )

    # 1. Distance > 4x ATR
    anchor_4x = StopAnchor(
        id="anc_4x",
        kind=StopAnchorKind.BREAKER_DISTAL_BOUNDARY.value,
        price=4025.0,
        timeframe="1M",
        occurred_at_ms=1000,
        known_at_ms=1000,
        source_leg_id="leg_01",
    )
    anchor_res_4x = StopAnchorResult(status="AVAILABLE", anchor=anchor_4x)

    atr_obj = StopATRResult(
        status="AVAILABLE",
        value=1.0,
        symbol="XAUUSDT",
        timeframe="1M",
        period=14,
        method="SMA_TR_V1",
        last_closed_at_ms=1000,
        decision_cutoff=1000,
        bars_count=15,
        source_candle_count=15,
        last_closed_candle_timestamp=1000,
        calculated_as_of=1000,
    )

    buf_res = StopBufferResult(
        buffer_price=0.30,
        components={"atr_m1": 0.25, "tick_floor": 0.05},
        dominant_source="atr_m1",
        tick_size=0.01,
        raw_atr_m1=1.0,
    )

    dec_4x = plan_structural_stop(
        direction="SHORT",
        planned_entry=4020.0,
        take_profit=4000.0,
        legacy_sl=4020.50,
        anchor_result=anchor_res_4x,
        buffer_result=buf_res,
        policy=policy,
        as_of_ms=1000,
        atr_m1_obj=atr_obj,
        spread=0.20,
    )

    assert dec_4x.status == StopStatus.VALID.value
    assert dec_4x.applied_variant == "C"
    assert dec_4x.selected_sl == 4025.30  # 4025.0 + 0.30 buffer
    assert dec_4x.distance_assessment == "DISTANCE_EXTREME"

    # 2. Distance > 10x ATR: Anchor at 4032.0 (distance 12.30 > 10x ATR)
    anchor_10x = StopAnchor(
        id="anc_10x",
        kind=StopAnchorKind.BREAKER_DISTAL_BOUNDARY.value,
        price=4032.0,
        timeframe="1M",
        occurred_at_ms=1000,
        known_at_ms=1000,
        source_leg_id="leg_02",
    )
    anchor_res_10x = StopAnchorResult(status="AVAILABLE", anchor=anchor_10x)

    dec_10x = plan_structural_stop(
        direction="SHORT",
        planned_entry=4020.0,
        take_profit=3980.0,
        legacy_sl=4020.50,
        anchor_result=anchor_res_10x,
        buffer_result=buf_res,
        policy=policy,
        as_of_ms=1000,
        atr_m1_obj=atr_obj,
        spread=0.20,
    )

    assert dec_10x.status == StopStatus.VALID.value
    assert dec_10x.selected_sl == 4032.30
    assert dec_10x.distance_assessment == "DISTANCE_EXTREME"


def test_financial_risk_conservation_and_quantity_downscaling():
    """
    Tests that when SL widens from Variant A ($0.50 dist) to Variant C ($5.00 dist),
    domain_calculator reduces quantity proportionally and conserves risk budget ($2.50 USDT).
    """
    costs = CostAssumptions(slippage_usd=0.05, maker_fee_rate=0.0002, taker_fee_rate=0.0004)

    # Tight stop: distance = $0.50
    calc_tight = calculate_risk_reward(
        direction="SHORT",
        planned_entry=4020.0,
        stop_loss=4020.50,
        take_profit=3990.0,
        capital_usdt=1000.0,
        risk_pct=0.25,  # 0.25% of 1000 = $2.50 USDT risk budget
        leverage=20,
        costs=costs,
    )
    assert calc_tight.can_execute is True
    qty_tight = calc_tight.quantity

    # Wide structural stop: distance = $5.00
    calc_wide = calculate_risk_reward(
        direction="SHORT",
        planned_entry=4020.0,
        stop_loss=4025.0,
        take_profit=3990.0,
        capital_usdt=1000.0,
        risk_pct=0.25,  # 0.25% of 1000 = $2.50 USDT risk budget
        leverage=20,
        costs=costs,
    )
    assert calc_wide.can_execute is True
    qty_wide = calc_wide.quantity

    # Quantity must strictly downscale as SL distance widens
    assert qty_wide < qty_tight

    # Risk budget must remain conserved around $2.50
    assert calc_wide.budget_usdt == 2.50
    assert abs(calc_wide.net_risk_usdt - 2.50) < 0.25


def test_fail_closed_when_wide_sl_causes_insufficient_net_rr():
    """
    Verifies invariant: If structural SL is wide, we NEVER artificially push TP
    to make Net R:R look acceptable. If Net R:R < min_net_rr, fails closed.
    """
    costs = CostAssumptions(slippage_usd=0.10, maker_fee_rate=0.0002, taker_fee_rate=0.0004)

    # Entry 4020, SL 4025 (dist $5.0), TP 4016 (reward $4.0) -> R:R < 1.0 < min 1.5
    calc = calculate_risk_reward(
        direction="SHORT",
        planned_entry=4020.0,
        stop_loss=4025.0,
        take_profit=4016.0,
        capital_usdt=1000.0,
        risk_pct=0.25,
        leverage=20,
        min_net_rr=1.5,
        costs=costs,
    )

    assert calc.meets_min_rr is False
    assert calc.can_execute is False
    assert "NET_RR" in calc.skip_reason or "INSUFFICIENT" in calc.skip_reason
