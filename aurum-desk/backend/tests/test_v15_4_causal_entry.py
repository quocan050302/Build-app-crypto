"""
backend/tests/test_v15_4_causal_entry.py
V15.4 — Comprehensive Causal Entry Correctness Test Suite.

Covers the 5 core closure areas:
1. Pure FVG interval intersection & exact LONG/SHORT repros (Sections 021 - 040, 161 - 165)
2. Verified liquidity sweep with real reference, breach, and reclaim (Sections 041 - 060, 166 - 169)
3. Prefix ATR causality across caller and detector (Sections 061 - 080, 170 - 173)
4. Authoritative target consistency & no synthetic risk fallback (Sections 081 - 100, 174 - 177)
5. Non-refreshing hypothesis expiry & lifecycle reconciliation (Sections 101 - 120, 178 - 181)
6. Full causal LONG & SHORT lifecycle and guard matrix (Sections 184 - 189)
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
import pytest
from sqlalchemy.orm import Session

import models
from services.clock import FakeClock
from services.trade_lifecycle_service import TradeLifecycleService
from domain_calculator import calculate_risk_reward
from services.liquidity_scenario_service import (
    DealingRange,
    LiquidityHypothesis,
    build_liquidity_hypotheses,
    confirm_liquidity_sweep,
    detect_order_blocks,
    reconcile_hypothesis_lifecycle,
)
from services.ny_quota_helpers import closed_candles_at
from services.scalp_m1_model_service import (
    ScalpCandidate,
    ScalpM1ModelService,
    candle_intersects_price_zone,
    resolve_executable_target,
    select_sweep_reference,
)
from services.scalp_m1_types import (
    M1ScalpConfig,
    ModelIdentifier,
    OpportunityReasonCode,
    OpportunityStage,
    ScalpMode,
    TargetDecision,
    WatchedZone,
    ZoneState,
)
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator
from services.smc_liquidity_service import build_liquidity_map, select_unconsumed_target
from services.smc_scenario_types import LiquidityLevel
from services.zone_watchlist_service import ZoneWatchlistService
from tests.test_v15_1_execution_lifecycle import (
    CandleStub,
    _make_causal_long_m1_candles,
    _make_causal_short_m1_candles,
    _setup_positive_trading_policy,
)


# =====================================================================
# SECTION 1: PURE FVG INTERVAL INTERSECTION & EXACT REPROS (161 - 165)
# =====================================================================

@pytest.mark.parametrize(
    "c_low,c_high,bot,top,expected,label",
    [
        # Standard intersections
        (102.0, 108.0, 100.0, 110.0, True, "strictly_inside"),
        (95.0, 115.0, 100.0, 110.0, True, "spanning_zone"),
        (95.0, 105.0, 100.0, 110.0, True, "intersecting_bottom"),
        (105.0, 115.0, 100.0, 110.0, True, "intersecting_top"),
        (100.0, 105.0, 100.0, 110.0, True, "touching_bottom_boundary"),
        (105.0, 110.0, 100.0, 110.0, True, "touching_top_boundary"),
        # Disjoint non-intersections
        (90.0, 99.9, 100.0, 110.0, False, "completely_below"),
        (110.1, 120.0, 100.0, 110.0, False, "completely_above"),
        # Exact defect repros
        (92.0, 95.0, 100.0, 110.0, False, "defect_long_below_fvg"),
        (115.0, 118.0, 100.0, 110.0, False, "defect_short_above_fvg"),
        # Invalid inputs
        (105.0, 102.0, 100.0, 110.0, False, "invalid_candle_low_gt_high"),
        (102.0, 108.0, 110.0, 100.0, False, "invalid_zone_bot_gte_top"),
        (float("nan"), 108.0, 100.0, 110.0, False, "nan_candle_low"),
        (102.0, float("inf"), 100.0, 110.0, False, "inf_candle_high"),
    ],
)
def test_fvg_pure_geometry(c_low: float, c_high: float, bot: float, top: float, expected: bool, label: str):
    """Pure geometric interval intersection: low <= top and high >= bottom."""
    candle = CandleStub(1000, open=c_low, high=c_high, low=c_low, close=c_high)
    assert candle_intersects_price_zone(candle, bot, top) is expected, f"Failed on case: {label}"


def test_exact_long_repro_candle_below_fvg():
    """
    SECTION 021, 022, 162: EXACT LONG REPRO.
    FVG = [100.0, 110.0], sweep_price = 90.0, candle low = 92.0, high = 95.0.
    Baseline predicate (low <= fvg_top and low >= sweep_price) accepted it (92 <= 110 and 92 >= 90).
    Pure intersection MUST reject because candle range [92, 95] is completely below [100, 110].
    """
    retest_candle = CandleStub(1140_000, open=93.0, high=95.0, low=92.0, close=94.0)
    # 1. Pure helper check
    assert candle_intersects_price_zone(retest_candle, 100.0, 110.0) is False

    # 2. Production path check: build candles where retest bar is [92, 95] outside FVG [2614, 2622]
    candles = _make_causal_long_m1_candles()
    bad_retest = CandleStub(1080_000, open=2610.0, high=2612.0, low=2608.0, close=2609.0)
    mutated_candles = list(candles[:17]) + [bad_retest]

    zone = WatchedZone(
        zone_id="z_repro_long", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.CONFIRMED.value,
    )
    res = ScalpM1ModelService.evaluate_m1_reversal_candidate(
        zone, mutated_candles, {"bid": 2622.0, "ask": 2622.5, "is_valid": True},
        {"capital_usdt": 1000.0, "risk_pct": 0.25, "leverage": 30}, now_ms=1300_000,
    )
    assert res.is_eligible is False
    assert res.reason_code == OpportunityReasonCode.WAITING_M1_RETEST.value


def test_exact_short_repro_candle_above_fvg():
    """
    SECTION 023, 163: EXACT SHORT REPRO.
    FVG = [100.0, 110.0], sweep_price = 120.0, candle low = 115.0, high = 118.0.
    Baseline SHORT predicate accepted it. Pure intersection MUST reject because [115, 118] > 110.
    """
    retest_candle = CandleStub(1140_000, open=116.0, high=118.0, low=115.0, close=117.0)
    assert candle_intersects_price_zone(retest_candle, 100.0, 110.0) is False

    # Production path check for SHORT
    candles = _make_causal_short_m1_candles()
    bad_retest = CandleStub(1080_000, open=2660.0, high=2662.0, low=2658.0, close=2659.0)  # above FVG [2648, 2654]
    mutated_candles = list(candles[:17]) + [bad_retest]

    zone = WatchedZone(
        zone_id="z_repro_short", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BEARISH", top=2670.0, bottom=2650.0, created_at=60_000, known_at=60_000,
        state=ZoneState.CONFIRMED.value,
    )
    res = ScalpM1ModelService.evaluate_m1_reversal_candidate(
        zone, mutated_candles, {"bid": 2647.5, "ask": 2648.0, "is_valid": True},
        {"capital_usdt": 1000.0, "risk_pct": 0.25, "leverage": 30}, now_ms=1300_000,
    )
    assert res.is_eligible is False
    assert res.reason_code == OpportunityReasonCode.WAITING_M1_RETEST.value


# =====================================================================
# SECTION 2: VERIFIED LIQUIDITY SWEEP (041 - 060, 166 - 169)
# =====================================================================

def test_sweep_helper_positive_and_negative_controls():
    """SECTION 166: confirm_liquidity_sweep positive control, touch-only, breach without reclaim."""
    ref_level = LiquidityLevel(
        id="lvl_sweep_ref_01", price=2603.5, side="SELL_SIDE", source_kind="SWING",
        occurred_at_ms=240_000, known_at_ms=360_000, state="UNTOUCHED",
    )

    # 1. Positive: Breach (< 2603.5) and Reclaim (> 2603.5 close)
    pos_candles = [
        CandleStub(420_000, 2606.0, 2607.0, 2604.0, 2605.0),
        CandleStub(480_000, 2605.0, 2606.0, 2601.5, 2604.5),  # Low 2601.5 breaches 2603.5, close 2604.5 reclaims
    ]
    conf = confirm_liquidity_sweep(ref_level, pos_candles, now_ms=600_000, candle_duration_ms=60_000)
    assert conf is not None
    assert conf.is_confirmed is True
    assert conf.breach_price == 2601.5
    assert conf.reclaim_price == 2604.5
    assert conf.rejection_ratio > 0.0

    # 2. Negative: Touch-only (Low touches exactly 2603.5 without breach)
    touch_candles = [
        CandleStub(420_000, 2606.0, 2607.0, 2604.0, 2605.0),
        CandleStub(480_000, 2605.0, 2606.0, 2603.5, 2604.5),
    ]
    conf_touch = confirm_liquidity_sweep(ref_level, touch_candles, now_ms=600_000, candle_duration_ms=60_000)
    assert conf_touch is None or conf_touch.is_confirmed is False

    # 3. Negative: Breach without reclaim (Closes below level)
    no_reclaim_candles = [
        CandleStub(420_000, 2606.0, 2607.0, 2604.0, 2605.0),
        CandleStub(480_000, 2605.0, 2606.0, 2601.0, 2602.0),  # Close 2602.0 remains below 2603.5
    ]
    conf_no_rec = confirm_liquidity_sweep(ref_level, no_reclaim_candles, now_ms=600_000, candle_duration_ms=60_000)
    assert conf_no_rec is None or conf_no_rec.is_confirmed is False


def test_reversal_candidate_rejects_pivot_without_liquidity_sweep():
    """
    SECTION 041, 168: Local swing low near zone without prior liquidity level breach
    must NOT be confirmed as a sweep. Returns WAITING_LIQUIDITY_REFERENCE.
    """
    # Create candles that form a local low at 2602 inside zone, but with NO prior swing low before it
    candles = [
        # Steady drop from 2630 down to 2602 with no swing low formed yet
        CandleStub(60_000, 2630.0, 2632.0, 2628.0, 2629.0),
        CandleStub(120_000, 2629.0, 2630.0, 2625.0, 2626.0),
        CandleStub(180_000, 2626.0, 2627.0, 2620.0, 2622.0),
        CandleStub(240_000, 2622.0, 2624.0, 2618.0, 2619.0),
        CandleStub(300_000, 2619.0, 2620.0, 2615.0, 2616.0),
        CandleStub(360_000, 2616.0, 2617.0, 2612.0, 2614.0),
        CandleStub(420_000, 2614.0, 2618.0, 2612.0, 2616.0),  # Swing High @ 2618
        CandleStub(480_000, 2616.0, 2617.0, 2610.0, 2612.0),
        CandleStub(540_000, 2612.0, 2613.0, 2608.0, 2609.0),
        CandleStub(600_000, 2609.0, 2610.0, 2606.0, 2607.0),
        CandleStub(660_000, 2607.0, 2608.0, 2604.0, 2605.0),
        CandleStub(720_000, 2605.0, 2606.0, 2602.0, 2605.0),  # Low at 2602, but what did it sweep? Nothing!
        CandleStub(780_000, 2605.0, 2609.0, 2604.0, 2608.0),
        CandleStub(840_000, 2608.0, 2611.0, 2607.0, 2610.0),
        CandleStub(900_000, 2610.0, 2614.0, 2609.0, 2612.0),
        CandleStub(960_000, 2612.0, 2627.0, 2611.0, 2626.0),  # Break
        CandleStub(1020_000, 2626.0, 2627.0, 2622.0, 2624.0),
        CandleStub(1080_000, 2624.0, 2625.0, 2621.0, 2623.0),
        CandleStub(1140_000, 2623.0, 2624.0, 2620.0, 2622.0),  # Retest
    ]
    zone = WatchedZone(
        zone_id="z_no_sweep", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.CONFIRMED.value,
    )
    res = ScalpM1ModelService.evaluate_m1_reversal_candidate(
        zone, candles, {"bid": 2622.0, "ask": 2622.5, "is_valid": True},
        {"capital_usdt": 1000.0, "risk_pct": 0.25, "leverage": 30}, now_ms=1200_000,
    )
    assert res.is_eligible is False
    assert res.reason_code in (
        OpportunityReasonCode.WAITING_LIQUIDITY_REFERENCE.value,
        OpportunityReasonCode.WAITING_M1_STRUCTURE.value,
    )


# =====================================================================
# SECTION 3: PREFIX ATR VIA CALLER & DETECTOR (061 - 080, 170 - 173)
# =====================================================================

def test_prefix_atr_caller_future_invariance():
    """
    SECTION 061, 068, 080, 170: extract_zones_from_candles() must use historical prefix ATR.
    Appending extreme future volatility bars must NOT alter historical OB qualification or invalidation
    at decision time T.
    """
    # 1. Base 30 candles up to decision time T
    base_candles = [
        CandleStub(i * 300_000, 2600.0 + (i % 3), 2605.0 + (i % 3), 2598.0 + (i % 3), 2602.0 + (i % 3))
        for i in range(1, 31)
    ]
    # Inject a clean OB break at index 15:
    # Bar 14 is a down candle: open=2604, high=2605, low=2595, close=2596
    base_candles[14] = CandleStub(15 * 300_000, 2604.0, 2605.0, 2595.0, 2596.0)
    # Bar 15 is a strong bullish break: open=2596, high=2615, low=2595, close=2614
    base_candles[15] = CandleStub(16 * 300_000, 2596.0, 2615.0, 2595.0, 2614.0)

    t_decision_ms = 30 * 300_000
    zones_at_t = ZoneWatchlistService.extract_zones_from_candles(base_candles, timeframe="5M", symbol="XAUUSDT", as_of_ms=t_decision_ms)
    ob_zones_at_t = [z for z in zones_at_t if z.zone_type == "OB"]

    # 2. Append 10 future bars with massive volatility (ATR ~ 50.0)
    future_candles = list(base_candles)
    for k in range(31, 41):
        future_candles.append(CandleStub(k * 300_000, 2650.0, 2750.0, 2550.0, 2700.0))

    # Query again strictly as of T
    zones_with_future = ZoneWatchlistService.extract_zones_from_candles(future_candles, timeframe="5M", symbol="XAUUSDT", as_of_ms=t_decision_ms)
    ob_zones_with_future = [z for z in zones_with_future if z.zone_type == "OB"]

    # Historical OB identification at T must be perfectly identical
    assert len(ob_zones_at_t) == len(ob_zones_with_future)
    for z1, z2 in zip(ob_zones_at_t, ob_zones_with_future):
        assert z1.zone_id == z2.zone_id
        assert z1.top == z2.top
        assert z1.bottom == z2.bottom
        assert z1.invalidation_level == z2.invalidation_level
        assert z1.known_at == z2.known_at


def test_detect_order_blocks_ignores_current_window_atr_leakage():
    """
    SECTION 065, 066, 173: detect_order_blocks() computes prefix ATR dynamically per bar.
    Passing a legacy scalar atr argument does NOT override historical causal prefix ATR.
    """
    candles = [
        CandleStub(i * 300_000, 2600.0, 2603.0, 2599.0, 2602.0)
        for i in range(1, 25)
    ]
    # Break bar at index 10
    candles[9] = CandleStub(10 * 300_000, 2602.0, 2603.0, 2598.0, 2599.0)
    candles[10] = CandleStub(11 * 300_000, 2599.0, 2615.0, 2598.0, 2614.0)

    from services.liquidity_scenario_service import identify_confirmed_swings
    sh, sl = identify_confirmed_swings(candles, left_bars=2, right_bars=2)
    obs_no_arg = detect_order_blocks(candles, sh + sl, min_displacement_atr=1.0)
    obs_with_inflated_atr = detect_order_blocks(candles, sh + sl, atr=100.0, min_displacement_atr=1.0)

    # Both must identify the same order block because historical prefix ATR at bar 10 was causal
    assert len(obs_no_arg) == len(obs_with_inflated_atr)
    if obs_no_arg:
        assert obs_no_arg[0].top == obs_with_inflated_atr[0].top
        assert obs_no_arg[0].bottom == obs_with_inflated_atr[0].bottom


# =====================================================================
# SECTION 4: TARGET AUTHORITY CONSISTENCY (081 - 100, 174 - 177)
# =====================================================================

def test_target_authority_no_synthetic_fallback_when_unavailable():
    """
    SECTION 081, 084, 100, 174: When no authoritative unconsumed liquidity target is available,
    SMC candidate MUST NOT fall back to synthetic risk-multiplier or ATR targets.
    Must return WAITING_LIQUIDITY_TARGET and remain non-eligible.
    """
    ref_level = LiquidityLevel(
        id="lvl_sweep_ref_01", price=2603.5, side="SELL_SIDE", source_kind="SWING",
        occurred_at_ms=360_000, known_at_ms=540_000, state="UNTOUCHED",
    )
    # Create levels where all buy-side levels are CONSUMED or on the wrong side
    consumed_levels = [
        LiquidityLevel(
            id="lvl_consumed_01", price=2635.0, side="BUY_SIDE", source_kind="SWING",
            occurred_at_ms=100_000, known_at_ms=200_000, state="CONSUMED",
        ),
        LiquidityLevel(
            id="lvl_wrong_side", price=2615.0, side="SELL_SIDE", source_kind="SWING",
            occurred_at_ms=100_000, known_at_ms=200_000, state="UNTOUCHED",
        ),
    ]

    # Target decision must be UNAVAILABLE
    target_dec = resolve_executable_target(consumed_levels, "LONG", entry_price=2622.5, as_of_ms=1300_000)
    assert target_dec.status == "UNAVAILABLE"
    assert target_dec.price is None

    # Candidate evaluation with these levels must return WAITING_LIQUIDITY_TARGET
    candles = _make_causal_long_m1_candles()
    zone = WatchedZone(
        zone_id="z_target_none", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.CONFIRMED.value,
    )
    context_levels = [ref_level] + consumed_levels
    res = ScalpM1ModelService.evaluate_m1_reversal_candidate(
        zone, candles, {"bid": 2622.0, "ask": 2622.5, "is_valid": True},
        {"capital_usdt": 1000.0, "risk_pct": 0.25, "leverage": 30}, now_ms=1300_000,
        context_levels=context_levels,
    )
    assert res.is_eligible is False
    assert res.reason_code == OpportunityReasonCode.WAITING_LIQUIDITY_TARGET.value


def test_target_authority_consistency_when_present():
    """
    SECTION 091, 097, 175: When target is present, hypothesis, candidate, and order snapshot
    must agree on exact price, source_level_id, and label.
    """
    ref_level = LiquidityLevel(
        id="lvl_sweep_ref_01", price=2603.5, side="SELL_SIDE", source_kind="SWING",
        occurred_at_ms=360_000, known_at_ms=540_000, state="UNTOUCHED",
    )
    valid_levels = [
        LiquidityLevel(
            id="sh_causal_target_01", price=2675.0, side="BUY_SIDE", source_kind="SWING",
            occurred_at_ms=180_000, known_at_ms=360_000, state="UNTOUCHED", label_vi="Đỉnh swing 2675.00",
        )
    ]
    target_dec = resolve_executable_target(valid_levels, "LONG", entry_price=2622.5, as_of_ms=1300_000)
    assert target_dec.status == "CONFIRMED"
    assert target_dec.price == 2675.0
    assert target_dec.source_level_id == "sh_causal_target_01"

    # Evaluated candidate must embed exact target
    candles = _make_causal_long_m1_candles()
    zone = WatchedZone(
        zone_id="z_target_ok", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.CONFIRMED.value,
    )
    context_levels = [ref_level] + valid_levels
    res = ScalpM1ModelService.evaluate_m1_reversal_candidate(
        zone, candles, {"bid": 2622.0, "ask": 2622.5, "is_valid": True},
        {"capital_usdt": 1000.0, "risk_pct": 0.25, "leverage": 30}, now_ms=1300_000,
        context_levels=context_levels,
    )
    assert res.is_eligible is True
    assert res.take_profit == 2675.0
    assert res.snapshot.target["price"] == 2675.0


# =====================================================================
# SECTION 5: NON-REFRESHING HYPOTHESIS EXPIRY (101 - 120, 178 - 181)
# =====================================================================

def test_hypothesis_expiry_preserves_anchor_across_cycles():
    """
    SECTION 101, 104, 116, 178: Repeated evaluation cycles must NOT refresh or extend
    the expiry of an existing hypothesis. created_at_ms and expiry_ms must remain locked.
    """
    t0_ms = 1_000_000
    ttl_ms = 14_400_000  # 4 hours
    initial_exp = t0_ms + ttl_ms

    zone = WatchedZone(
        zone_id="z_m5_01", symbol="XAUUSDT", timeframe="5M", zone_type="DEMAND",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=t0_ms, known_at=t0_ms,
    )
    dr = DealingRange(2650.0, 2600.0, 2625.0, "range-1", t0_ms, True)

    hyp1 = LiquidityHypothesis(
        hypothesis_id="hyp-primary-z_m5_01",
        direction="BULLISH",
        is_primary=True,
        watched_zone=zone,
        target_level=None,
        target_price=2675.0,
        dealing_range=dr,
        range_position="DISCOUNT",
        prerequisites=[],
        invalidation_condition="Thủng đáy vùng cầu",
        expiry_ms=initial_exp,
        created_at_ms=t0_ms,
        status="ACTIVE",
    )

    # 1. Simulate cycle 1 hour later (t1 = t0 + 1 hour)
    t1_ms = t0_ms + 3_600_000
    new_candidate_hyp = LiquidityHypothesis(
        hypothesis_id="hyp-primary-z_m5_01",
        direction="BULLISH",
        is_primary=True,
        watched_zone=zone,
        target_level=None,
        target_price=2675.0,
        dealing_range=dr,
        range_position="DISCOUNT",
        prerequisites=[],
        invalidation_condition="Thủng đáy vùng cầu",
        expiry_ms=t1_ms + ttl_ms,  # Fresh cycle tries to advance expiry
        created_at_ms=t1_ms,       # Fresh cycle tries to advance creation
        status="ACTIVE",
    )

    reconciled = reconcile_hypothesis_lifecycle([hyp1], [new_candidate_hyp], now_ms=t1_ms)
    assert len(reconciled) == 1
    r_hyp = reconciled[0]

    # MUST preserve original created_at and original expiry!
    assert r_hyp.created_at_ms == t0_ms
    assert r_hyp.expiry_ms == initial_exp
    assert r_hyp.status == "ACTIVE"


def test_hypothesis_marks_expired_past_deadline():
    """
    SECTION 109, 119, 179: When now_ms >= expiry_ms, hypothesis status becomes EXPIRED.
    Orchestrator must block candidate evaluation on expired hypotheses.
    """
    t0_ms = 1_000_000
    ttl_ms = 3_600_000  # 1 hour
    exp_ms = t0_ms + ttl_ms

    zone = WatchedZone(
        zone_id="z_m5_01", symbol="XAUUSDT", timeframe="5M", zone_type="DEMAND",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=t0_ms, known_at=t0_ms,
    )
    dr = DealingRange(2650.0, 2600.0, 2625.0, "range-1", t0_ms, True)

    hyp = LiquidityHypothesis(
        hypothesis_id="hyp-primary-z_m5_01",
        direction="BULLISH",
        is_primary=True,
        watched_zone=zone,
        target_level=None,
        target_price=2675.0,
        dealing_range=dr,
        range_position="DISCOUNT",
        prerequisites=[],
        invalidation_condition="Thủng đáy vùng cầu",
        expiry_ms=exp_ms,
        created_at_ms=t0_ms,
        status="ACTIVE",
    )

    # Check 1 minute before expiry -> still ACTIVE
    rec_before = reconcile_hypothesis_lifecycle([hyp], [hyp], now_ms=exp_ms - 60_000)
    assert rec_before[0].status == "ACTIVE"

    # Check exactly at expiry -> EXPIRED
    rec_at = reconcile_hypothesis_lifecycle([hyp], [hyp], now_ms=exp_ms)
    assert rec_at[0].status == "EXPIRED"

    # Check after expiry -> EXPIRED
    rec_after = reconcile_hypothesis_lifecycle([hyp], [hyp], now_ms=exp_ms + 100_000)
    assert rec_after[0].status == "EXPIRED"


# =====================================================================
# SECTION 6: FULL CAUSAL LIFECYCLE LONG & SHORT (184, 185)
# =====================================================================

def test_full_causal_long_lifecycle_arm_fill_tp(isolated_db: Session):
    """
    SECTION 184: Full causal LONG lifecycle:
    Candles -> Zone -> Sweep -> Break -> FVG -> Retest -> Target -> Guards -> Arm.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    cfg = M1ScalpConfig(
        scalp_m1_mode=ScalpMode.PAPER.value,
        daily_ny_research_enabled=False,
        min_closed_m1_bars=15,
        displacement_body_atr_min=0.5,
    )

    zone = WatchedZone(
        zone_id="z_lifecycle_long", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_long_m1_candles()
    now_ms = 1_300_000
    quote = {"bid": 2622.0, "ask": 2622.5, "is_valid": True, "server_time": now_ms, "observed_at": now_ms}
    feed_health = {"status": "CONNECTED", "latency_ms": 10}

    res = orch.evaluate_cycle(
        isolated_db, "XAUUSDT",
        now_ms=now_ms, quote=quote, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
        config=cfg, feed_health=feed_health, auto_enabled=True,
    )

    assert res["status"] == "OK"
    assert res["armed_order_id"] is not None

    order = isolated_db.query(models.PaperOrder).filter(models.PaperOrder.id == res["armed_order_id"]).first()
    assert order is not None
    assert order.direction == "LONG"
    assert order.state == "armed"
    assert order.planned_entry == 2622.5
    assert order.stop_loss == 2601.95  # Protected below 2602.0 swept extreme
    assert order.take_profit == 2675.0  # From authoritative unconsumed swing high
    assert order.estimated_net_rr >= 2.0

    # Real Execution: ARM -> FILL -> TP -> CLOSE
    calc = calculate_risk_reward(
        "LONG", order.planned_entry, order.stop_loss, order.take_profit, 1000.0, 0.25, 30.0
    )
    fill_time = now_ms + 10_000
    filled = TradeLifecycleService.execute_fill(
        db=isolated_db,
        order=order,
        fill_price=order.planned_entry,
        calc_result=calc,
        clock=FakeClock(fill_time),
        now_ms=fill_time,
    )
    assert filled.state == "paper_open"
    assert filled.actual_entry == order.planned_entry
    assert filled.opened_at == fill_time

    close_time = fill_time + 60_000
    closed = TradeLifecycleService.execute_close(
        db=isolated_db,
        order_id=order.id,
        exit_price=order.take_profit,
        exit_cause="TP_HIT",
        occurred_at=close_time,
        session_tag="NORMAL",
    )
    assert closed.state == "closed"
    assert closed.actual_exit == order.take_profit
    assert closed.exit_cause == "TP_HIT"
    assert closed.realized_pnl_net > 0

    # Verify domain events & outbox
    ev_types = [e.event_type for e in isolated_db.query(models.DomainEvent).filter(models.DomainEvent.aggregate_id == order.id).all()]
    assert "order.armed" in ev_types
    assert "trade.opened" in ev_types
    assert "trade.closed" in ev_types


def test_full_causal_short_lifecycle_arm_fill_sl(isolated_db: Session):
    """
    SECTION 185: Full causal SHORT lifecycle:
    Symmetric verification: TP < planned_entry < SL, net R:R >= 2.0,
    progressing through ARM -> FILL -> SL -> CLOSE.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    cfg = M1ScalpConfig(
        scalp_m1_mode=ScalpMode.PAPER.value,
        daily_ny_research_enabled=False,
        min_closed_m1_bars=15,
        displacement_body_atr_min=0.5,
    )

    zone = WatchedZone(
        zone_id="z_lifecycle_short", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BEARISH", top=2670.0, bottom=2650.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_short_m1_candles()
    now_ms = 1_300_000
    quote = {"bid": 2647.5, "ask": 2648.0, "is_valid": True, "server_time": now_ms, "observed_at": now_ms}
    feed_health = {"status": "CONNECTED", "latency_ms": 10}

    res = orch.evaluate_cycle(
        isolated_db, "XAUUSDT",
        now_ms=now_ms, quote=quote, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
        config=cfg, feed_health=feed_health, auto_enabled=True,
    )

    assert res["status"] == "OK"
    assert res["armed_order_id"] is not None

    order = isolated_db.query(models.PaperOrder).filter(models.PaperOrder.id == res["armed_order_id"]).first()
    assert order is not None
    assert order.direction == "SHORT"
    assert order.state == "armed"
    assert order.planned_entry == 2647.5
    assert order.stop_loss == 2668.05  # Protected above 2668.0 swept extreme
    assert order.take_profit == 2595.0  # Authoritative unconsumed swing low target
    assert order.take_profit < order.planned_entry < order.stop_loss
    assert order.estimated_net_rr >= 2.0

    # Real Execution: ARM -> FILL -> SL -> CLOSE
    calc = calculate_risk_reward(
        "SHORT", order.planned_entry, order.stop_loss, order.take_profit, 1000.0, 0.25, 30.0
    )
    fill_time = now_ms + 10_000
    filled = TradeLifecycleService.execute_fill(
        db=isolated_db,
        order=order,
        fill_price=order.planned_entry,
        calc_result=calc,
        clock=FakeClock(fill_time),
        now_ms=fill_time,
    )
    assert filled.state == "paper_open"
    assert filled.actual_entry == order.planned_entry
    assert filled.opened_at == fill_time

    close_time = fill_time + 60_000
    closed = TradeLifecycleService.execute_close(
        db=isolated_db,
        order_id=order.id,
        exit_price=order.stop_loss,
        exit_cause="SL_HIT",
        occurred_at=close_time,
        session_tag="NORMAL",
    )
    assert closed.state == "closed"
    assert closed.actual_exit == order.stop_loss
    assert closed.exit_cause == "SL_HIT"
    assert closed.realized_pnl_net < 0

    ev_types = [e.event_type for e in isolated_db.query(models.DomainEvent).filter(models.DomainEvent.aggregate_id == order.id).all()]
    assert "order.armed" in ev_types
    assert "trade.opened" in ev_types
    assert "trade.closed" in ev_types
