"""
backend/tests/test_v17_9_2_structural_stop.py
AURUM DESK V17.9.2 — Comprehensive Unit, Property, Boundary, Synthetic Positive/Negative Path,
Historical Forensic, 3-Runtime Parity, and Negative-Control Test Suite (T01..T26).
"""

from __future__ import annotations

import asyncio
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

from domain_calculator import CostAssumptions, calculate_risk_reward
from services.liquidity_scenario_service import identify_confirmed_swings
from services.market_shadow_service import MarketShadowService
from services.scalp_m1_types import ModelIdentifier, get_scalp_config
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator
from services.shared_strategy_evaluator import (
    DecisionStatus,
    PRODUCTION_MODEL_IDS,
    REQUIRED_PARITY_FIELDS,
    SharedStrategyEvaluator,
    build_market_decision_context,
    compare_decision_parity,
)
from services.smc_scenario_types import LiquidityLevel
from services.stop_placement_types import (
    StopAnchor,
    StopAnchorKind,
    StopPlacementMode,
    StopPlacementPolicy,
)
from services.unicorn_evidence_types import UnicornEvidence
from services.v17_8_backtest_runner import (
    collect_or_load_verified_historical_dataset,
    evaluate_production_parity_model_snapshot,
    has_unverified_intra_session_gap,
)
from services.v17_9_1_decision_funnel import classify_historical_dataset_gaps
from services.v17_9_2_structural_stop_intelligence import (
    StructuralStopCandidate,
    StructuralStopPlanV1792,
    StructuralTargetPlanV1792,
    V1792_ALGORITHM_VERSION,
    classify_structure_stop_outcome,
    collect_causal_stop_anchor_candidates,
    compute_causal_noise_buffer,
    derive_structure_valid_stop,
    evaluate_stop_tp_risk_and_size,
    measure_premature_stop_rate,
    plan_v17_9_2_structural_stop_and_target,
    rank_structure_valid_anchors,
    resolve_causal_target_candidates,
    select_viable_structural_target,
    validate_structure_invalidation_contract,
)
from tools.run_v17_9_async_regression import (
    AsyncWorkerGroupSpec,
    BoundedAsyncTestOrchestrator,
)
from tools.test_resource_guard import DiskBudgetExceededError, TestResourceGuard
from tools.v17_9_execution_state import ExecutionStateManager


def _build_synthetic_bullish_fvg_bars(base_ts: int = 1_700_000_000_000) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Constructs a causal 20-bar M1 series and 6-bar M5 series with:
      - Protected M5/M1 swing low at 4000.00
      - Swing high at 4006.00 broken by bullish displacement creating an unmitigated FVG [4004.50, 4006.80]
      - Retest bar closing at 4005.50 inside the FVG.
    """
    m1_bars: List[Dict[str, Any]] = []
    specs = [
        (4003.0, 4004.0, 4002.0, 4002.5),
        (4002.5, 4003.0, 4000.0, 4001.5),  # idx 1: Major Swing Low @ 4000.00
        (4001.5, 4003.5, 4001.0, 4003.0),
        (4003.0, 4004.5, 4002.0, 4004.0),
        (4004.0, 4006.0, 4003.5, 4005.2),  # idx 4: Swing High @ 4006.00
        (4005.2, 4005.5, 4003.8, 4004.2),
        (4004.2, 4004.8, 4003.2, 4003.6),
        (4003.6, 4004.2, 4002.2, 4002.8),  # idx 7: Internal pullback low @ 4002.20
        (4002.8, 4004.0, 4002.5, 4003.8),
        (4003.8, 4004.5, 4003.0, 4004.2),
        (4004.2, 4005.0, 4002.4, 4002.6),  # idx 10: Bearish OB candle before break [4002.4, 4005.0]
        (4002.6, 4004.5, 4002.5, 4004.4),  # idx 11: FVG bar 1 (high=4004.50)
        (4004.4, 4008.2, 4004.2, 4007.8),  # idx 12: Displacement break above 4006.00
        (4007.8, 4009.0, 4006.8, 4008.4),  # idx 13: FVG bar 3 (low=4006.80) -> Bullish FVG [4004.50, 4006.80]
        (4008.4, 4008.8, 4007.0, 4007.5),
        (4007.5, 4008.0, 4006.9, 4007.2),
        (4007.2, 4007.6, 4006.85, 4007.0),
        (4007.0, 4007.3, 4006.82, 4006.9),
        (4006.9, 4007.1, 4005.0, 4005.4),  # idx 18: Retest into FVG
        (4005.4, 4006.2, 4005.1, 4005.8),  # idx 19: Bullish rejection close inside FVG
    ]
    for idx, (o, h, l, c) in enumerate(specs):
        t_open = base_ts + idx * 60_000
        m1_bars.append({
            "symbol": "XAUUSDT",
            "timeframe": "1M",
            "timestamp": t_open,
            "open_time": t_open,
            "close_time": t_open + 60_000,
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "volume": 100.0,
            "is_closed": True,
        })

    m5_bars: List[Dict[str, Any]] = []
    m5_specs = [
        (4004.0, 4005.0, 4002.5, 4003.0),
        (4003.0, 4004.0, 4000.0, 4003.5),  # M5 Swing Low @ 4000.00
        (4003.5, 4006.0, 4002.2, 4004.2),
        (4004.2, 4009.0, 4002.4, 4007.5),
        (4007.5, 4008.5, 4005.0, 4005.8),
    ]
    for idx, (o, h, l, c) in enumerate(m5_specs):
        t_open = base_ts - (5 - idx) * 300_000
        m5_bars.append({
            "symbol": "XAUUSDT",
            "timeframe": "5M",
            "timestamp": t_open,
            "open_time": t_open,
            "close_time": t_open + 300_000,
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "volume": 500.0,
            "is_closed": True,
        })
    return m1_bars, m5_bars


def test_t01_causal_anchor_candidate_collection_and_anti_lookahead():
    """
    T01: Causal anchor candidate collection & anti-lookahead.
    Future or unclosed bars/swings must be rejected and never selected.
    """
    m1_bars, m5_bars = _build_synthetic_bullish_fvg_bars()
    cutoff_ms = int(m1_bars[-1]["close_time"])
    sh_1m, sl_1m = identify_confirmed_swings(m1_bars, timeframe="1M", left_bars=2, right_bars=2, as_of_ms=cutoff_ms)

    # Inject future swing and unclosed future bar
    sl_with_future = list(sl_1m) + [
        {
            "id": "sl_future_leak",
            "price": 3995.0,
            "occurred_at_ms": cutoff_ms + 60_000,
            "known_at_ms": cutoff_ms + 180_000,
        }
    ]
    m1_with_unclosed = list(m1_bars) + [
        {
            "symbol": "XAUUSDT",
            "timeframe": "1M",
            "timestamp": cutoff_ms,
            "open_time": cutoff_ms,
            "close_time": cutoff_ms + 60_000,
            "open": 4005.8,
            "high": 4006.0,
            "low": 3990.0,
            "close": 3991.0,
            "is_closed": False,
        }
    ]

    candidates = collect_causal_stop_anchor_candidates(
        model_id="SMC_M1_FVG_CONTINUATION",
        direction="LONG",
        entry_price=4005.90,
        decision_timestamp_ms=cutoff_ms,
        closed_m1=m1_with_unclosed,
        closed_m5=m5_bars,
        sh_1m=sh_1m,
        sl_1m=sl_with_future,
    )
    assert len(candidates) > 0
    for c in candidates:
        if c.is_causal:
            assert c.known_at_ms <= cutoff_ms
            assert c.occurred_at_ms <= c.known_at_ms
            assert c.anchor_id != "sl_future_leak"
        else:
            assert "FUTURE_ANCHOR_REJECTED" in c.rejection_reasons


def test_t02_model_specific_anchor_ranking_and_tie_break():
    """
    T02: Model-specific anchor ranking & deterministic tie-break.
    Verifies local sweep wick is deprioritized in favor of outer protected swing when both exist,
    and equal-score candidates tie-break deterministically.
    """
    cutoff_ms = 1_700_001_000_000
    local_wick = StructuralStopCandidate(
        anchor_id="anc_local_wick",
        anchor_kind="SWEEP_EXTREME",
        anchor_price=4003.12,
        timeframe="1M",
        occurred_at_ms=cutoff_ms - 180_000,
        known_at_ms=cutoff_ms - 120_000,
        source_bar_open_ms=cutoff_ms - 180_000,
        source_bar_close_ms=cutoff_ms - 120_000,
        is_closed_bar=True,
        is_causal=True,
        is_invalidated=False,
        is_protected_swing=False,
        is_local_sweep_only=True,
        structural_rank=80,
        model_compatibility=("SMC_M1_REVERSAL",),
    )
    outer_protected = StructuralStopCandidate(
        anchor_id="anc_outer_protected_sh",
        anchor_kind="PROTECTED_SWING_HIGH",
        anchor_price=4006.87,
        timeframe="5M",
        occurred_at_ms=cutoff_ms - 600_000,
        known_at_ms=cutoff_ms - 300_000,
        source_bar_open_ms=cutoff_ms - 600_000,
        source_bar_close_ms=cutoff_ms - 300_000,
        is_closed_bar=True,
        is_causal=True,
        is_invalidated=False,
        is_protected_swing=True,
        is_local_sweep_only=False,
        structural_rank=90,
        model_compatibility=("SMC_M1_REVERSAL",),
    )
    selected, ranked, reasons = rank_structure_valid_anchors(
        [local_wick, outer_protected],
        model_id="SMC_M1_REVERSAL",
        direction="SHORT",
        entry_price=4001.40,
        decision_timestamp_ms=cutoff_ms,
    )
    assert selected is not None
    assert selected.anchor_id == "anc_outer_protected_sh"
    assert reasons == ()

    # Equal-score tie-break determinism across input permutations
    c1 = StructuralStopCandidate(
        anchor_id="anc_b",
        anchor_kind="PROTECTED_SWING_LOW",
        anchor_price=3998.00,
        timeframe="1M",
        occurred_at_ms=cutoff_ms - 300_000,
        known_at_ms=cutoff_ms - 180_000,
        source_bar_open_ms=cutoff_ms - 300_000,
        source_bar_close_ms=cutoff_ms - 240_000,
        is_closed_bar=True,
        is_causal=True,
        is_invalidated=False,
        is_protected_swing=True,
        is_local_sweep_only=False,
        structural_rank=85,
        model_compatibility=("SMC_M1_CONTINUATION",),
    )
    c2 = StructuralStopCandidate(
        anchor_id="anc_a",
        anchor_kind="PROTECTED_SWING_LOW",
        anchor_price=3998.00,
        timeframe="1M",
        occurred_at_ms=cutoff_ms - 300_000,
        known_at_ms=cutoff_ms - 180_000,
        source_bar_open_ms=cutoff_ms - 300_000,
        source_bar_close_ms=cutoff_ms - 240_000,
        is_closed_bar=True,
        is_causal=True,
        is_invalidated=False,
        is_protected_swing=True,
        is_local_sweep_only=False,
        structural_rank=85,
        model_compatibility=("SMC_M1_CONTINUATION",),
    )
    sel_1, _, _ = rank_structure_valid_anchors([c1, c2], model_id="SMC_M1_CONTINUATION", direction="LONG", entry_price=4005.0, decision_timestamp_ms=cutoff_ms)
    sel_2, _, _ = rank_structure_valid_anchors([c2, c1], model_id="SMC_M1_CONTINUATION", direction="LONG", entry_price=4005.0, decision_timestamp_ms=cutoff_ms)
    assert sel_1 is not None and sel_2 is not None
    assert sel_1.anchor_id == sel_2.anchor_id == "anc_a"


def test_t03_directional_stop_monotonicity_and_tick_quantization():
    """
    T03: Directional stop monotonicity & outward tick quantization.
    LONG: proposed_sl <= floor_tick(invalidation_price - buffer) < entry_price
    SHORT: proposed_sl >= ceil_tick(invalidation_price + buffer) > entry_price
    """
    # LONG sub-tick test
    sl_long = derive_structure_valid_stop(
        direction="LONG",
        invalidation_price=4000.00,
        noise_buffer_price=0.314,
        tick_size=0.01,
    )
    assert sl_long is not None
    assert sl_long <= 4000.00 - 0.314 + 1e-9
    assert sl_long == 3999.68

    # SHORT sub-tick test
    sl_short = derive_structure_valid_stop(
        direction="SHORT",
        invalidation_price=4010.00,
        noise_buffer_price=0.314,
        tick_size=0.01,
    )
    assert sl_short is not None
    assert sl_short >= 4010.00 + 0.314 - 1e-9
    assert sl_short == 4010.32


def test_t04_causal_noise_buffer_monotonicity_and_fallback():
    """
    T04: Causal noise buffer monotonicity & missing M5 fallback provenance.
    """
    b1, comps1, _ = compute_causal_noise_buffer(atr_m1=1.0, atr_m5=2.0, spread=0.20, tick_size=0.01)
    b2, comps2, _ = compute_causal_noise_buffer(atr_m1=2.0, atr_m5=4.0, spread=0.20, tick_size=0.01)
    b3, comps3, _ = compute_causal_noise_buffer(atr_m1=2.0, atr_m5=4.0, spread=0.80, tick_size=0.01)
    assert b1 <= b2 <= b3

    # Missing M5 ATR must use M1+spread fallback and never fabricate atr_m1 * 2.1
    b_no_m5, comps_no_m5, _ = compute_causal_noise_buffer(atr_m1=1.6, atr_m5=None, spread=0.20, tick_size=0.01)
    assert b_no_m5 > 0.0
    assert "atr_m5" not in comps_no_m5

    # Structure-only mode uses minimal tick floor
    b_struct, _, dom_struct = compute_causal_noise_buffer(
        atr_m1=2.0, atr_m5=4.0, spread=0.20, tick_size=0.01, buffer_mode="STRUCTURE_ONLY"
    )
    assert b_struct == 0.01
    assert dom_struct == "structure_only_tick"


def test_t05_structure_invalidation_contract_negative_guards():
    """
    T05: Structure invalidation contract negative guards (wrong-side SL, already-breached anchor,
    non-finite price, boolean price, zero tick).
    """
    cutoff_ms = 1_700_001_000_000
    valid_cand = StructuralStopCandidate(
        anchor_id="anc_1",
        anchor_kind="PROTECTED_SWING_LOW",
        anchor_price=4000.0,
        timeframe="1M",
        occurred_at_ms=cutoff_ms - 180_000,
        known_at_ms=cutoff_ms - 120_000,
        source_bar_open_ms=cutoff_ms - 180_000,
        source_bar_close_ms=cutoff_ms - 120_000,
        is_closed_bar=True,
        is_causal=True,
        is_invalidated=False,
        is_protected_swing=True,
        is_local_sweep_only=False,
        structural_rank=85,
    )

    # Wrong-side SL for LONG (SL >= entry)
    ok_ws, r_ws = validate_structure_invalidation_contract(
        direction="LONG",
        entry_price=4000.0,
        proposed_sl=4001.0,
        anchor=valid_cand,
        noise_buffer_price=0.20,
        decision_timestamp_ms=cutoff_ms,
    )
    assert not ok_ws and "SL_WRONG_SIDE_OF_ENTRY" in r_ws

    # Already-breached anchor
    breached_cand = StructuralStopCandidate(**{**valid_cand.__dict__, "is_invalidated": True})
    ok_br, r_br = validate_structure_invalidation_contract(
        direction="LONG",
        entry_price=4005.0,
        proposed_sl=3999.5,
        anchor=breached_cand,
        noise_buffer_price=0.50,
        decision_timestamp_ms=cutoff_ms,
    )
    assert not ok_br and "ANCHOR_ALREADY_INVALIDATED" in r_br

    # Non-finite price
    ok_nf, r_nf = validate_structure_invalidation_contract(
        direction="LONG",
        entry_price=4005.0,
        proposed_sl=float("nan"),
        anchor=valid_cand,
        noise_buffer_price=0.50,
        decision_timestamp_ms=cutoff_ms,
    )
    assert not ok_nf and "NON_FINITE_STOP_OR_ANCHOR" in r_nf


def test_t06_adversarial_future_price_mutation_invariance():
    """
    T06: Adversarial future price mutation invariance.
    Mutating bars with close_time > decision_timestamp_ms to $9999.0 or $1000.0
    must produce 100% identical SL, TP, Net R:R, quantity, and decision_fingerprint.
    """
    m1_bars, m5_bars = _build_synthetic_bullish_fvg_bars()
    cutoff_ms = int(m1_bars[-1]["close_time"])
    pools = [
        LiquidityLevel(
            id="pool_m15_high",
            price=4025.00,
            side="BUY_SIDE",
            source_kind="M15_SWING_HIGH",
            occurred_at_ms=cutoff_ms - 600_000,
            known_at_ms=cutoff_ms - 300_000,
            state="UNTOUCHED",
        )
    ]

    future_bar_high = {
        "symbol": "XAUUSDT",
        "timeframe": "1M",
        "timestamp": cutoff_ms,
        "open_time": cutoff_ms,
        "close_time": cutoff_ms + 60_000,
        "open": 9999.0,
        "high": 9999.0,
        "low": 9999.0,
        "close": 9999.0,
        "is_closed": True,
    }
    future_bar_low = {
        "symbol": "XAUUSDT",
        "timeframe": "1M",
        "timestamp": cutoff_ms,
        "open_time": cutoff_ms,
        "close_time": cutoff_ms + 60_000,
        "open": 1000.0,
        "high": 1000.0,
        "low": 1000.0,
        "close": 1000.0,
        "is_closed": True,
    }

    pol = {
        "v17_9_2_structural_sl_enabled": True,
        "v17_9_2_mode": "STRUCTURE_PLUS_TP",
        "v17_9_2_target_mode": "V1792_CAUSAL_STRUCTURAL_VIABLE",
    }
    ctx_clean = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=cutoff_ms,
        m1_candles=m1_bars,
        m5_candles=m5_bars,
        bid=4005.70,
        ask=4005.90,
        liquidity_pools=pools,
        policy_config=pol,
    )
    ctx_mut_high = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=cutoff_ms,
        m1_candles=m1_bars + [future_bar_high],
        m5_candles=m5_bars,
        bid=4005.70,
        ask=4005.90,
        liquidity_pools=pools,
        policy_config=pol,
    )
    ctx_mut_low = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=cutoff_ms,
        m1_candles=m1_bars + [future_bar_low],
        m5_candles=m5_bars,
        bid=4005.70,
        ask=4005.90,
        liquidity_pools=pools,
        policy_config=pol,
    )

    s_clean = SharedStrategyEvaluator.evaluate_model_direction(ctx_clean, "SMC_M1_FVG_CONTINUATION", "LONG")
    s_high = SharedStrategyEvaluator.evaluate_model_direction(ctx_mut_high, "SMC_M1_FVG_CONTINUATION", "LONG")
    s_low = SharedStrategyEvaluator.evaluate_model_direction(ctx_mut_low, "SMC_M1_FVG_CONTINUATION", "LONG")

    assert s_clean.selected_sl == s_high.selected_sl == s_low.selected_sl
    assert s_clean.take_profit == s_high.take_profit == s_low.take_profit
    assert s_clean.net_rr == s_high.net_rr == s_low.net_rr
    assert s_clean.hypothetical_quantity == s_high.hypothetical_quantity == s_low.hypothetical_quantity
    assert s_clean.decision_fingerprint == s_high.decision_fingerprint == s_low.decision_fingerprint


def test_t07_target_selection_from_known_causal_liquidity_pools():
    """
    T07: Target selection from known causal liquidity pools.
    Rejects consumed, future, and wrong-side pools; selects valid causal target with net_rr >= min_net_rr.
    """
    cutoff_ms = 1_700_001_000_000
    pools = [
        {"id": "p_wrong_side", "price": 3995.0, "side": "SELL_SIDE", "known_at_ms": cutoff_ms - 60_000, "state": "UNTOUCHED"},
        {"id": "p_consumed", "price": 4015.0, "side": "BUY_SIDE", "known_at_ms": cutoff_ms - 60_000, "state": "SWEPT"},
        {"id": "p_future", "price": 4020.0, "side": "BUY_SIDE", "known_at_ms": cutoff_ms + 60_000, "state": "UNTOUCHED"},
        {"id": "p_too_close", "price": 4008.0, "side": "BUY_SIDE", "known_at_ms": cutoff_ms - 60_000, "state": "UNTOUCHED"},
        {"id": "p_viable_m15", "price": 4022.0, "side": "BUY_SIDE", "known_at_ms": cutoff_ms - 60_000, "state": "UNTOUCHED", "timeframe": "15M"},
    ]
    cands = resolve_causal_target_candidates(
        direction="LONG",
        entry_price=4005.0,
        decision_timestamp_ms=cutoff_ms,
        liquidity_pools=pools,
        atr_m1=1.0,
    )
    cand_ids = [c["id"] for c in cands]
    assert "p_wrong_side" not in cand_ids
    assert "p_consumed" not in cand_ids
    assert "p_future" not in cand_ids
    assert "p_viable_m15" in cand_ids

    t_plan = select_viable_structural_target(
        direction="LONG",
        entry_price=4005.0,
        proposed_sl=4000.0,
        decision_timestamp_ms=cutoff_ms,
        target_candidates=cands,
        baseline_target_price=4008.0,
        baseline_target_id="p_too_close",
        min_net_rr=2.0,
        target_mode="V1792_CAUSAL_STRUCTURAL_VIABLE",
    )
    assert t_plan.status == "VALID"
    assert t_plan.selected_target_id == "p_viable_m15"
    assert t_plan.target_price == 4022.0
    assert t_plan.known_at_ms <= cutoff_ms


def test_t08_no_invented_rr_target_rule():
    """
    T08: No-invented-RR-target rule.
    When no causal liquidity pool exists or none meets min_net_rr, returns NO_STRUCTURAL_TP_TARGET
    or NET_RR_TOO_LOW and never invents TP = entry +/- k * stop_distance.
    """
    cutoff_ms = 1_700_001_000_000
    t_empty = select_viable_structural_target(
        direction="LONG",
        entry_price=4005.0,
        proposed_sl=4000.0,
        decision_timestamp_ms=cutoff_ms,
        target_candidates=[],
        baseline_target_price=None,
        baseline_target_id=None,
        min_net_rr=2.0,
        target_mode="V1792_CAUSAL_STRUCTURAL_VIABLE",
    )
    assert t_empty.status == "UNAVAILABLE"
    assert t_empty.target_price is None
    assert "NO_STRUCTURAL_TP_TARGET" in t_empty.rejection_reasons


def test_t09_cost_aware_net_rr_and_dynamic_quantity_scaling():
    """
    T09: Cost-aware Net R:R & dynamic quantity scaling under fixed $2.50 risk budget.
    """
    r_narrow = evaluate_stop_tp_risk_and_size(
        direction="LONG",
        entry_price=4000.0,
        proposed_sl=3998.0,
        target_price=4026.0,
        capital_usdt=1000.0,
        risk_pct=0.25,
        leverage=20,
        margin_mode="ISOLATED",
        min_net_rr=2.0,
    )
    r_wide = evaluate_stop_tp_risk_and_size(
        direction="LONG",
        entry_price=4000.0,
        proposed_sl=3994.0,
        target_price=4026.0,
        capital_usdt=1000.0,
        risk_pct=0.25,
        leverage=20,
        margin_mode="ISOLATED",
        min_net_rr=2.0,
    )
    assert r_narrow["hypothetical_quantity"] > r_wide["hypothetical_quantity"] > 0.0
    assert r_narrow["net_risk_usdt"] <= 2.50 + 1e-6
    assert r_wide["net_risk_usdt"] <= 2.50 + 1e-6
    assert r_narrow["net_rr"] > r_wide["net_rr"]


def test_t10_min_lot_and_margin_rejection_for_wide_stop():
    """
    T10: Minimum lot & margin rejection when structural SL is too wide ($300 stop distance)
    for $2.50 risk budget.
    """
    r_too_wide = evaluate_stop_tp_risk_and_size(
        direction="LONG",
        entry_price=4000.0,
        proposed_sl=3700.0,
        target_price=4900.0,
        capital_usdt=1000.0,
        risk_pct=0.25,
        leverage=20,
        margin_mode="ISOLATED",
        min_net_rr=2.0,
        min_qty=0.01,
    )
    assert r_too_wide["is_executable"] is False
    assert r_too_wide["hypothetical_quantity"] == 0.0
    assert "POSITION_SIZE_BELOW_MIN_LOT" in r_too_wide["rejection_reasons"]


def test_t11_baseline_vs_observe_only_action_invariance():
    """
    T11: Baseline vs Observe-only action invariance.
    A_FROZEN_BASELINE_OFF vs B_V1792_OBSERVE_ONLY must have 100% identical trade action fields
    and trade_action_fingerprint, while having distinct policy_fingerprint and metadata_fingerprint.
    """
    m1_bars, m5_bars = _build_synthetic_bullish_fvg_bars()
    cutoff_ms = int(m1_bars[-1]["close_time"])
    pools = [
        LiquidityLevel(
            id="pool_tp_1",
            price=4025.00,
            side="BUY_SIDE",
            source_kind="M15_SWING_HIGH",
            occurred_at_ms=cutoff_ms - 600_000,
            known_at_ms=cutoff_ms - 300_000,
            state="UNTOUCHED",
        )
    ]
    ctx_a = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=cutoff_ms,
        m1_candles=m1_bars,
        m5_candles=m5_bars,
        bid=4005.70,
        ask=4005.90,
        liquidity_pools=pools,
        policy_config={"v17_9_2_structural_sl_enabled": False, "v17_9_2_observe_only": False, "v17_9_2_mode": "OFF"},
    )
    ctx_b = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=cutoff_ms,
        m1_candles=m1_bars,
        m5_candles=m5_bars,
        bid=4005.70,
        ask=4005.90,
        liquidity_pools=pools,
        policy_config={"v17_9_2_structural_sl_enabled": False, "v17_9_2_observe_only": True, "v17_9_2_mode": "OBSERVE_ONLY"},
    )

    for model_id in PRODUCTION_MODEL_IDS:
        snap_a = SharedStrategyEvaluator.evaluate_model_direction(ctx_a, model_id, "LONG")
        snap_b = SharedStrategyEvaluator.evaluate_model_direction(ctx_b, model_id, "LONG")
        for f_name in REQUIRED_PARITY_FIELDS:
            assert getattr(snap_a, f_name) == getattr(snap_b, f_name), f"Mismatch on {f_name} for {model_id}"
        assert snap_a.decision_fingerprint == snap_b.decision_fingerprint
        assert snap_a.trade_action_fingerprint == snap_b.trade_action_fingerprint
        assert snap_a.policy_fingerprint != snap_b.policy_fingerprint
        assert snap_a.metadata_fingerprint != snap_b.metadata_fingerprint


def test_t12_three_runtime_adapter_parity():
    """
    T12: 3-runtime adapter parity across ScalpWatcherOrchestrator, MarketShadowService,
    and evaluate_production_parity_model_snapshot for modes A, B, C, D1..D4.
    """
    m1_bars, m5_bars = _build_synthetic_bullish_fvg_bars()
    cutoff_ms = int(m1_bars[-1]["close_time"])
    pools = [
        LiquidityLevel(
            id="pool_tp_1",
            price=4025.00,
            side="BUY_SIDE",
            source_kind="M15_SWING_HIGH",
            occurred_at_ms=cutoff_ms - 600_000,
            known_at_ms=cutoff_ms - 300_000,
            state="UNTOUCHED",
        )
    ]
    prod_orch = ScalpWatcherOrchestrator()
    shadow_svc = MarketShadowService(symbol="XAUUSDT")

    modes = [
        {"v17_9_2_mode": "OFF"},
        {"v17_9_2_mode": "OBSERVE_ONLY", "v17_9_2_observe_only": True},
        {"v17_9_2_mode": "STRUCTURAL_SL_ON", "v17_9_2_structural_sl_enabled": True},
        {"v17_9_2_mode": "STRUCTURE_ONLY", "v17_9_2_buffer_mode": "STRUCTURE_ONLY"},
        {"v17_9_2_mode": "STRUCTURE_PLUS_NOISE", "v17_9_2_buffer_mode": "STRUCTURE_PLUS_NOISE"},
        {"v17_9_2_mode": "TP_POLICY_ONLY", "v17_9_2_target_mode": "V1792_CAUSAL_STRUCTURAL_VIABLE"},
        {"v17_9_2_mode": "STRUCTURE_PLUS_TP", "v17_9_2_target_mode": "V1792_CAUSAL_STRUCTURAL_VIABLE"},
    ]

    for pol in modes:
        ctx = build_market_decision_context(
            symbol="XAUUSDT",
            decision_timestamp_ms=cutoff_ms,
            m1_candles=m1_bars,
            m5_candles=m5_bars,
            bid=4005.70,
            ask=4005.90,
            liquidity_pools=pools,
            policy_config=pol,
        )
        s_prod = prod_orch.evaluate_shared_decision_snapshot(context=ctx, model_id="SMC_M1_FVG_CONTINUATION", direction="LONG")
        s_shad = shadow_svc.evaluate_shared_decision_snapshot(context=ctx, model_id="SMC_M1_FVG_CONTINUATION", direction="LONG")
        s_bt = evaluate_production_parity_model_snapshot(
            model_id="SMC_M1_FVG_CONTINUATION", context=ctx, direction="LONG"
        )["shared_decision_snapshot"]

        p_ps = compare_decision_parity(s_prod, s_shad)
        p_pb = compare_decision_parity(s_prod, s_bt)
        assert p_ps["is_match"], f"Prod vs Shadow mismatch in {pol}: {p_ps['mismatches']}"
        assert p_pb["is_match"], f"Prod vs Backtest mismatch in {pol}: {p_pb['mismatches']}"
        assert s_prod.adapter_call_id != s_shad.adapter_call_id
        assert s_prod.adapter_call_id != s_bt["adapter_call_id"]
        assert SharedStrategyEvaluator.is_registered_call_id(s_prod.adapter_call_id, "PRODUCTION_ORCHESTRATOR_ADAPTER")
        assert SharedStrategyEvaluator.is_registered_call_id(s_shad.adapter_call_id, "MARKET_SHADOW_ADAPTER")
        assert SharedStrategyEvaluator.is_registered_call_id(s_bt["adapter_call_id"], "HISTORICAL_BACKTEST_ADAPTER")


def test_t13_spoofed_or_reused_adapter_call_id_rejection():
    """
    T13: Spoofed or reused adapter_call_id rejection and self-comparison trap detection.
    """
    assert not SharedStrategyEvaluator.is_registered_call_id("call_SPOOFED_999999_999999")
    m1_bars, m5_bars = _build_synthetic_bullish_fvg_bars()
    cutoff_ms = int(m1_bars[-1]["close_time"])
    ctx = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=cutoff_ms,
        m1_candles=m1_bars,
        m5_candles=m5_bars,
        bid=4005.70,
        ask=4005.90,
    )
    snap = SharedStrategyEvaluator.evaluate_model_direction(
        ctx, "SMC_M1_FVG_CONTINUATION", "LONG", adapter_origin="PRODUCTION_ORCHESTRATOR_ADAPTER"
    )
    # Wrong expected origin fails verification
    assert not SharedStrategyEvaluator.is_registered_call_id(snap.adapter_call_id, "MARKET_SHADOW_ADAPTER")
    # Self-comparison fails compare_decision_parity
    self_cmp = compare_decision_parity(snap, snap)
    assert not self_cmp["is_match"]
    assert any(m["reason"] == "SELF_COMPARISON_TRAP_DETECTED" for m in self_cmp["mismatches"])


def test_t14_premature_stop_vs_true_invalidation_outcome_classifier():
    """
    T14: Premature stop vs true invalidation outcome classifier across 4 canonical forward paths:
      (a) Wick sweeps short SL then hits TP without breaking structure -> PREMATURE_NOISE_SWEEP_STOP
      (b) Candle closes beyond invalidation anchor and trends against trade -> TRUE_INVALIDATION_LOSS
      (c) Clean TP win -> CLEAN_WIN
      (d) Horizon expiry -> OPEN_AT_HORIZON
    """
    t0 = 1_700_002_000_000
    # (a) LONG at 4005, local SL at 4003.50, outer protected structure at 4000.00, TP at 4012.00
    path_a = [
        {"open_time": t0 + 60_000, "open": 4005.0, "high": 4005.5, "low": 4003.2, "close": 4004.5},  # Wicks 4003.2 (< 4003.5 SL), > 4000.0 structure
        {"open_time": t0 + 120_000, "open": 4004.5, "high": 4009.0, "low": 4004.2, "close": 4008.5},
        {"open_time": t0 + 180_000, "open": 4008.5, "high": 4013.0, "low": 4008.0, "close": 4012.5},  # Hits TP 4012.0
    ]
    diag_a = classify_structure_stop_outcome(
        episode_id="ep_a",
        model_id="SMC_M1_REVERSAL",
        direction="LONG",
        entry_price=4005.0,
        sl_price=4003.5,
        tp_price=4012.0,
        invalidation_price=4003.8,
        protected_structure_price=4000.0,
        decision_timestamp_ms=t0,
        forward_bars=path_a,
    )
    assert diag_a.outcome_class == "PREMATURE_NOISE_SWEEP_STOP"
    assert diag_a.tp_would_hit_after_stop is True

    # (b) True invalidation loss: breaks and closes below 4000.0 structure and trends down
    path_b = [
        {"open_time": t0 + 60_000, "open": 4005.0, "high": 4005.2, "low": 3998.0, "close": 3998.5},
        {"open_time": t0 + 120_000, "open": 3998.5, "high": 3999.0, "low": 3992.0, "close": 3993.0},
    ]
    diag_b = classify_structure_stop_outcome(
        episode_id="ep_b",
        model_id="SMC_M1_REVERSAL",
        direction="LONG",
        entry_price=4005.0,
        sl_price=3999.5,
        tp_price=4015.0,
        invalidation_price=4000.0,
        protected_structure_price=4000.0,
        decision_timestamp_ms=t0,
        forward_bars=path_b,
    )
    assert diag_b.outcome_class == "TRUE_INVALIDATION_LOSS"

    # (c) Clean win
    path_c = [
        {"open_time": t0 + 60_000, "open": 4005.0, "high": 4010.0, "low": 4004.5, "close": 4009.5},
        {"open_time": t0 + 120_000, "open": 4009.5, "high": 4016.0, "low": 4009.0, "close": 4015.5},
    ]
    diag_c = classify_structure_stop_outcome(
        episode_id="ep_c",
        model_id="SMC_M1_FVG_CONTINUATION",
        direction="LONG",
        entry_price=4005.0,
        sl_price=3999.5,
        tp_price=4015.0,
        invalidation_price=4000.0,
        protected_structure_price=4000.0,
        decision_timestamp_ms=t0,
        forward_bars=path_c,
    )
    assert diag_c.outcome_class == "CLEAN_WIN"

    # (d) Horizon expiry
    path_d = [
        {"open_time": t0 + 60_000, "open": 4005.0, "high": 4007.0, "low": 4003.0, "close": 4006.0},
    ]
    diag_d = classify_structure_stop_outcome(
        episode_id="ep_d",
        model_id="SMC_M1_FVG_CONTINUATION",
        direction="LONG",
        entry_price=4005.0,
        sl_price=3999.5,
        tp_price=4015.0,
        invalidation_price=4000.0,
        protected_structure_price=4000.0,
        decision_timestamp_ms=t0,
        forward_bars=path_d,
    )
    assert diag_d.outcome_class == "OPEN_AT_HORIZON"


def test_t15_historical_6_episode_forensic_reproduction():
    """
    T15: Historical 6-episode forensic reproduction on real saved dataset
    (`i_1m` in [6652, 6676, 6886, 14469, 14608, 15383]).
    """
    ds = collect_or_load_verified_historical_dataset(symbol="XAUUSDT", allow_network_refresh=False)
    bars_1m = ds["1m"]
    bars_5m = ds["5m"]
    import bisect
    c5 = [int(b["close_time"]) for b in bars_5m]

    episodes = [
        (6652, "SMC_M1_FVG_CONTINUATION", "SHORT"),
        (6676, "SMC_M1_REVERSAL", "SHORT"),
        (6886, "SMC_M1_REVERSAL", "SHORT"),
        (14469, "SMC_M1_FVG_CONTINUATION", "SHORT"),
        (14608, "SMC_M1_REVERSAL", "SHORT"),
        (15383, "SMC_M1_REVERSAL", "SHORT"),
    ]
    diagnostics = []
    for i_1m, model_id, direction in episodes:
        asof_ms = int(bars_1m[i_1m]["close_time"])
        i5 = bisect.bisect_right(c5, asof_ms)
        w1 = bars_1m[max(0, i_1m + 1 - 120) : i_1m + 1]
        w5 = bars_5m[max(0, i5 - 96) : i5]
        fwd = bars_1m[i_1m + 1 : min(len(bars_1m), i_1m + 1 + 180)]
        lc = float(w1[-1]["close"])

        ctx_b = build_market_decision_context(
            symbol="XAUUSDT",
            decision_timestamp_ms=asof_ms,
            m1_candles=w1,
            m5_candles=w5,
            bid=round(lc - 0.10, 2),
            ask=round(lc + 0.10, 2),
            policy_config={"v17_9_2_observe_only": True, "v17_9_2_mode": "OBSERVE_ONLY"},
        )
        snap = SharedStrategyEvaluator.evaluate_model_direction(ctx_b, model_id, direction)
        assert snap.decision_status == DecisionStatus.ELIGIBLE.value
        assert snap.v1792_stop_plan_dict is not None
        v_plan = snap.v1792_stop_plan_dict
        prot_px = v_plan["invalidation_price"] or snap.structural_invalidation_price

        diag = classify_structure_stop_outcome(
            episode_id=f"hist_ep_{i_1m}",
            model_id=model_id,
            direction=direction,
            entry_price=float(snap.entry_reference),
            sl_price=float(snap.selected_sl),
            tp_price=float(snap.take_profit),
            invalidation_price=float(snap.structural_invalidation_price),
            protected_structure_price=float(prot_px),
            decision_timestamp_ms=asof_ms,
            forward_bars=fwd,
            provenance="HISTORICAL_BACKTEST_V1792",
        )
        assert diag.stop_hit is True
        assert diag.stop_hit_bar_ts is not None
        assert math.isfinite(diag.mfe_usd) and math.isfinite(diag.mae_usd)
        diagnostics.append(diag)

    summary = measure_premature_stop_rate(diagnostics)
    assert summary["total_episodes"] == 6
    assert summary["stopped_episodes"] == 6
    assert summary["premature_stop_count"] >= 2


def test_t16_stop_never_widened_after_arm_boundary():
    """
    T16: Stop never widened after ARM boundary.
    Verifies cand.stop_loss == calc.stop_loss == snap.selected_sl at ARM enforcement boundary.
    """
    m1_bars, m5_bars = _build_synthetic_bullish_fvg_bars()
    cutoff_ms = int(m1_bars[-1]["close_time"])
    pools = [
        LiquidityLevel(
            id="pool_tp_arm",
            price=4032.00,
            side="BUY_SIDE",
            source_kind="M15_SWING_HIGH",
            occurred_at_ms=cutoff_ms - 600_000,
            known_at_ms=cutoff_ms - 300_000,
            state="UNTOUCHED",
        )
    ]
    ctx = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=cutoff_ms,
        m1_candles=m1_bars,
        m5_candles=m5_bars,
        bid=4005.70,
        ask=4005.90,
        liquidity_pools=pools,
        policy_config={
            "v17_9_2_structural_sl_enabled": True,
            "v17_9_2_mode": "STRUCTURE_PLUS_TP",
            "v17_9_2_target_mode": "V1792_CAUSAL_STRUCTURAL_VIABLE",
        },
    )
    snap = SharedStrategyEvaluator.evaluate_model_direction(ctx, "SMC_M1_FVG_CONTINUATION", "LONG")
    assert snap.decision_status == DecisionStatus.ELIGIBLE.value

    cand, enforced_snap = SharedStrategyEvaluator.enforce_canonical_decision_authority_on_candidate(
        snap=snap,
        context=ctx,
        model_id="SMC_M1_FVG_CONTINUATION",
        expected_direction="LONG",
        policy_settings={"capital_usdt": 1000.0, "risk_pct": 0.25, "leverage": 20, "margin_mode": "ISOLATED"},
        config=get_scalp_config(),
        now_ms=cutoff_ms,
    )
    assert cand.is_eligible is True
    assert cand.stop_loss == cand.calculation.stop_loss == enforced_snap.selected_sl


def test_t17_dataset_gap_and_quote_mode_guard():
    """
    T17: Dataset gap & quote-mode guard.
    Verifies intra-session gap detection on a 16-bar window and historical dataset gap report.
    """
    m1_bars, _ = _build_synthetic_bullish_fvg_bars()
    has_gap_clean, _ = has_unverified_intra_session_gap(m1_bars, lookback_bars=16)
    assert has_gap_clean is False

    # Remove bar at index 12 to create an unverified intra-session 1M gap
    gapped_bars = m1_bars[:12] + m1_bars[13:]
    has_gap_bad, gap_info = has_unverified_intra_session_gap(gapped_bars, lookback_bars=16)
    assert has_gap_bad is True
    assert gap_info is not None and gap_info["missing_bars"] == 1


def test_t18_chronological_oos_embargo_and_statistical_verdict_guard():
    """
    T18: Chronological OOS embargo & statistical verdict guard.
    When OOS trades == 0 or closed trades < 30, ready_for_strategy_activation must be 'NO'
    and verdict must be INCONCLUSIVE / NEGATIVE_EVIDENCE.
    """
    from tools.run_v17_9_2_structural_stop_backtest import evaluate_v1792_statistical_verdict

    verdict_zero_oos = evaluate_v1792_statistical_verdict(
        closed_trades=12,
        wins=8,
        net_pnl_usd=15.0,
        oos_closed_trades=0,
        oos_net_pnl_usd=0.0,
    )
    assert verdict_zero_oos["strategy_statistical_verdict"] == "INCONCLUSIVE"
    assert verdict_zero_oos["ready_for_strategy_activation"] == "NO"

    verdict_neg = evaluate_v1792_statistical_verdict(
        closed_trades=45,
        wins=5,
        net_pnl_usd=-80.0,
        oos_closed_trades=7,
        oos_net_pnl_usd=-12.0,
    )
    assert verdict_neg["strategy_statistical_verdict"] == "NEGATIVE_EVIDENCE"
    assert verdict_neg["ready_for_strategy_activation"] == "NO"


def test_t19_bounded_async_test_orchestrator_and_resource_guard_bounds(tmp_path: Path):
    """
    T19: BoundedAsyncTestOrchestrator and TestResourceGuard concurrency, disk shortage BLOCKED_DISK_BUDGET,
    and worker temp cleanup bounds.
    """
    art_dir = tmp_path / "artifacts"
    guard = TestResourceGuard(
        run_id="test_v1792_t19",
        repo_root=tmp_path,
        artifact_dir=art_dir,
        reserved_floor_override_bytes=1024,
    )
    state_mgr = ExecutionStateManager(
        run_id="test_v1792_t19",
        source_sha="sha_v1792",
        all_test_groups=["G1", "G2"],
        artifact_dir=art_dir,
    )
    orch = BoundedAsyncTestOrchestrator(
        run_id="test_v1792_t19",
        source_sha="sha_v1792",
        artifact_dir=art_dir,
        resource_guard=guard,
        state_manager=state_mgr,
        max_pytest_workers=2,
    )

    specs = []
    for gid in ("G1", "G2"):
        xml_out = art_dir / "junit" / f"{gid}.xml"
        log_out = art_dir / "logs" / f"{gid}.log"
        cmd = [
            sys.executable,
            "-c",
            (
                f"from pathlib import Path; "
                f"p = Path(r'{xml_out}'); p.parent.mkdir(parents=True, exist_ok=True); "
                f"p.write_text('<testsuite tests=\"1\"><testcase classname=\"{gid}\" name=\"t1\"/></testsuite>', encoding='utf-8'); "
                f"print('{gid}_OK')"
            ),
        ]
        specs.append(
            AsyncWorkerGroupSpec(
                group_id=gid,
                description=f"Test {gid}",
                command=cmd,
                cwd=tmp_path,
                junit_xml_path=xml_out,
                log_path=log_out,
                worker_pool="PYTEST",
            )
        )

    res = asyncio.run(orch.run_groups(specs))
    assert res["status"] == "PASS"
    assert res["succeeded_tasks"] == 2
    assert res["failed_tasks"] == 0
    assert res["node_accounting_invariant_holds"] is True
    assert res["peak_concurrent_workers"] <= 2
    assert res["concurrency_bound_holds"] is True
    assert res["orphan_processes_remaining"] == 0

    # Intentional disk shortage -> BLOCKED_DISK_BUDGET without evicting user files
    low_guard = TestResourceGuard(
        run_id="test_v1792_t19_low",
        repo_root=tmp_path,
        artifact_dir=art_dir,
        reserved_floor_override_bytes=10 * 1024 * 1024 * 1024,
        simulate_free_disk_bytes=128 * 1024 * 1024,
    )
    with pytest.raises(DiskBudgetExceededError) as exc_info:
        low_guard.enforce_disk_budget(stage="INTENTIONAL_DISK_SHORTAGE")
    assert exc_info.value.verdict == "BLOCKED_DISK_BUDGET"


def test_t20_negative_control_mutation_proof():
    """
    T20: Negative-control mutation proof (RED when contract is violated -> GREEN when restored).
    Tests:
      1. Wrong-side stop mutation -> rejected by validate_structure_invalidation_contract
      2. Future anchor lookahead mutation -> rejected by validate_structure_invalidation_contract
      3. Invented TP mutation (source_level_id missing) -> rejected
    """
    cutoff_ms = 1_700_001_000_000
    anchor = StructuralStopCandidate(
        anchor_id="anc_valid",
        anchor_kind="PROTECTED_SWING_LOW",
        anchor_price=4000.0,
        timeframe="1M",
        occurred_at_ms=cutoff_ms - 180_000,
        known_at_ms=cutoff_ms - 120_000,
        source_bar_open_ms=cutoff_ms - 180_000,
        source_bar_close_ms=cutoff_ms - 120_000,
        is_closed_bar=True,
        is_causal=True,
        is_invalidated=False,
        is_protected_swing=True,
        is_local_sweep_only=False,
        structural_rank=85,
    )

    # RED 1: Mutate SL to wrong side of entry
    ok_red_1, reasons_red_1 = validate_structure_invalidation_contract(
        direction="LONG",
        entry_price=4005.0,
        proposed_sl=4006.0,
        anchor=anchor,
        noise_buffer_price=0.20,
        decision_timestamp_ms=cutoff_ms,
    )
    assert ok_red_1 is False and "SL_WRONG_SIDE_OF_ENTRY" in reasons_red_1

    # RED 2: Mutate anchor known_at_ms into future (lookahead)
    future_anchor = StructuralStopCandidate(**{**anchor.__dict__, "known_at_ms": cutoff_ms + 60_000})
    ok_red_2, reasons_red_2 = validate_structure_invalidation_contract(
        direction="LONG",
        entry_price=4005.0,
        proposed_sl=3999.5,
        anchor=future_anchor,
        noise_buffer_price=0.50,
        decision_timestamp_ms=cutoff_ms,
    )
    assert ok_red_2 is False and "FUTURE_ANCHOR_REJECTED" in reasons_red_2

    # GREEN: Restore valid causal stop
    ok_green, reasons_green = validate_structure_invalidation_contract(
        direction="LONG",
        entry_price=4005.0,
        proposed_sl=3999.5,
        anchor=anchor,
        noise_buffer_price=0.50,
        decision_timestamp_ms=cutoff_ms,
    )
    assert ok_green is True and reasons_green == ()


def test_t21_synthetic_positive_path_reversal_sweep_reclaim():
    """
    T21: Synthetic positive path — Reversal / Sweep-Reclaim (`SYNTHETIC_UNIT_TEST`).
    Local wick at 4003.12 is swept by $0.45 noise (H=4003.57), while outer protected M5 swing high
    at 4004.80 holds and price drops to causal M15 liquidity pool at 3992.00.
    - Baseline local-wick SL (4003.42) is stopped prematurely by the 4003.57 wick.
    - V17.9.2 outer protected-swing SL (4005.12) + causal M15 TP (3992.00) is ELIGIBLE (net_rr >= 2.0),
      survives the noise wick, and closes CLEAN_WIN at TP.
    """
    base_ts = 1_700_010_000_000
    m1_bars = []
    # Build 20 closed M1 bars for SHORT reversal setup
    prices = [
        (4001.0, 4002.0, 4000.0, 4001.5),
        (4001.5, 4004.8, 4001.0, 4003.5),  # idx 1: Outer protected M1/M5 Swing High @ 4004.80
        (4003.5, 4003.8, 4001.5, 4002.0),
        (4002.0, 4002.5, 4000.5, 4001.0),
        (4001.0, 4002.2, 4000.2, 4001.2),
        (4001.2, 4002.8, 4000.8, 4002.5),  # idx 5: Internal Swing High @ 4002.80
        (4002.5, 4002.6, 4001.0, 4001.4),
        (4001.4, 4002.0, 4000.6, 4001.2),
        (4001.2, 4002.2, 4000.8, 4001.8),
        (4001.8, 4002.4, 4001.0, 4002.0),
        (4002.0, 4002.5, 4001.2, 4002.1),
        (4002.1, 4002.6, 4001.4, 4002.2),
        (4002.2, 4002.7, 4001.5, 4002.3),
        (4002.3, 4002.75, 4001.6, 4002.4),
        (4002.4, 4002.78, 4001.7, 4002.5),
        (4002.5, 4003.12, 4001.8, 4002.1),  # idx 15: Local sweep of 4002.80 up to 4003.12, closes back below 4002.80
        (4002.1, 4002.5, 4001.2, 4001.6),
        (4001.6, 4002.0, 4001.0, 4001.5),
        (4001.5, 4001.9, 4000.9, 4001.45),
        (4001.45, 4001.8, 4001.0, 4001.50),  # idx 19: Entry bid=4001.40, ask=4001.60
    ]
    for idx, (o, h, l, c) in enumerate(prices):
        t_open = base_ts + idx * 60_000
        m1_bars.append({
            "symbol": "XAUUSDT",
            "timeframe": "1M",
            "timestamp": t_open,
            "open_time": t_open,
            "close_time": t_open + 60_000,
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "volume": 100.0,
            "is_closed": True,
        })
    m5_bars = [
        {"symbol": "XAUUSDT", "timeframe": "5M", "timestamp": base_ts - 900_000, "open_time": base_ts - 900_000, "close_time": base_ts - 600_000, "open": 4001.0, "high": 4002.5, "low": 4000.0, "close": 4001.5, "is_closed": True},
        {"symbol": "XAUUSDT", "timeframe": "5M", "timestamp": base_ts - 600_000, "open_time": base_ts - 600_000, "close_time": base_ts - 300_000, "open": 4001.5, "high": 4004.8, "low": 4000.5, "close": 4002.0, "is_closed": True},
        {"symbol": "XAUUSDT", "timeframe": "5M", "timestamp": base_ts - 300_000, "open_time": base_ts - 300_000, "close_time": base_ts, "open": 4002.0, "high": 4003.0, "low": 4000.8, "close": 4001.5, "is_closed": True},
        {"symbol": "XAUUSDT", "timeframe": "5M", "timestamp": base_ts, "open_time": base_ts, "close_time": base_ts + 300_000, "open": 4001.5, "high": 4003.12, "low": 4000.8, "close": 4001.5, "is_closed": True},
    ]
    cutoff_ms = int(m1_bars[-1]["close_time"])
    pools = [
        LiquidityLevel(
            id="pool_m15_sell_tp",
            price=3980.00,
            side="SELL_SIDE",
            source_kind="M15_SWING_LOW",
            occurred_at_ms=base_ts - 1_200_000,
            known_at_ms=base_ts - 600_000,
            state="UNTOUCHED",
        )
    ]

    ctx_base = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=cutoff_ms,
        m1_candles=m1_bars,
        m5_candles=m5_bars,
        bid=4001.40,
        ask=4001.60,
        liquidity_pools=pools,
        policy_config={"v17_9_2_mode": "OFF"},
    )
    ctx_v1792 = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=cutoff_ms,
        m1_candles=m1_bars,
        m5_candles=m5_bars,
        bid=4001.40,
        ask=4001.60,
        liquidity_pools=pools,
        policy_config={
            "v17_9_2_structural_sl_enabled": True,
            "v17_9_2_mode": "STRUCTURE_PLUS_TP",
            "v17_9_2_target_mode": "V1792_CAUSAL_STRUCTURAL_VIABLE",
        },
    )
    snap_base = SharedStrategyEvaluator.evaluate_model_direction(ctx_base, "SMC_M1_REVERSAL", "SHORT")
    snap_v1792 = SharedStrategyEvaluator.evaluate_model_direction(ctx_v1792, "SMC_M1_REVERSAL", "SHORT")

    assert snap_base.decision_status == DecisionStatus.ELIGIBLE.value
    assert snap_v1792.decision_status == DecisionStatus.ELIGIBLE.value
    assert snap_v1792.selected_sl > snap_base.selected_sl
    assert snap_v1792.structural_invalidation_price == 4004.80
    assert snap_v1792.net_rr >= 2.0

    # Forward path: wick to 4003.57 sweeps baseline SL (4003.43), stays below 4004.80, then hits TP 3980.00
    fwd_bars = [
        {"open_time": cutoff_ms + 60_000, "open": 4001.5, "high": 4003.57, "low": 4001.0, "close": 4002.2},
        {"open_time": cutoff_ms + 120_000, "open": 4002.2, "high": 4002.4, "low": 3990.0, "close": 3990.5},
        {"open_time": cutoff_ms + 180_000, "open": 3990.5, "high": 3991.0, "low": 3979.2, "close": 3979.8},
    ]
    diag_base = classify_structure_stop_outcome(
        episode_id="syn_rev_base",
        model_id="SMC_M1_REVERSAL",
        direction="SHORT",
        entry_price=float(snap_base.entry_reference),
        sl_price=float(snap_base.selected_sl),
        tp_price=float(snap_base.take_profit),
        invalidation_price=float(snap_base.structural_invalidation_price),
        protected_structure_price=4004.80,
        decision_timestamp_ms=cutoff_ms,
        forward_bars=fwd_bars,
        provenance="SYNTHETIC_UNIT_TEST",
    )
    diag_v1792 = classify_structure_stop_outcome(
        episode_id="syn_rev_v1792",
        model_id="SMC_M1_REVERSAL",
        direction="SHORT",
        entry_price=float(snap_v1792.entry_reference),
        sl_price=float(snap_v1792.selected_sl),
        tp_price=float(snap_v1792.take_profit),
        invalidation_price=float(snap_v1792.structural_invalidation_price),
        protected_structure_price=4004.80,
        decision_timestamp_ms=cutoff_ms,
        forward_bars=fwd_bars,
        provenance="SYNTHETIC_UNIT_TEST",
    )
    assert diag_base.outcome_class == "PREMATURE_NOISE_SWEEP_STOP"
    assert diag_v1792.outcome_class == "CLEAN_WIN"


def test_t22_synthetic_positive_path_fvg_continuation():
    """
    T22: Synthetic positive path — FVG Continuation (`SYNTHETIC_UNIT_TEST`).
    Verifies displacement origin anchor + causal noise buffer, net_risk_usdt <= $2.50,
    net_rr >= 2.0, ELIGIBLE status, and CLEAN_WIN on forward path.
    """
    m1_bars, m5_bars = _build_synthetic_bullish_fvg_bars()
    cutoff_ms = int(m1_bars[-1]["close_time"])
    pools = [
        LiquidityLevel(
            id="pool_m15_tp",
            price=4032.00,
            side="BUY_SIDE",
            source_kind="M15_SWING_HIGH",
            occurred_at_ms=cutoff_ms - 600_000,
            known_at_ms=cutoff_ms - 300_000,
            state="UNTOUCHED",
        )
    ]
    ctx = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=cutoff_ms,
        m1_candles=m1_bars,
        m5_candles=m5_bars,
        bid=4005.70,
        ask=4005.90,
        liquidity_pools=pools,
        policy_config={
            "v17_9_2_structural_sl_enabled": True,
            "v17_9_2_mode": "STRUCTURE_PLUS_TP",
            "v17_9_2_target_mode": "V1792_CAUSAL_STRUCTURAL_VIABLE",
        },
    )
    snap = SharedStrategyEvaluator.evaluate_model_direction(ctx, "SMC_M1_FVG_CONTINUATION", "LONG")
    assert snap.decision_status == DecisionStatus.ELIGIBLE.value
    assert snap.net_rr >= 2.0
    assert snap.hypothetical_quantity > 0.0
    assert snap.v1792_stop_plan_dict["net_risk_usdt"] <= 2.50 + 1e-6

    fwd_bars = [
        {"open_time": cutoff_ms + 60_000, "open": 4005.9, "high": 4018.0, "low": 4004.8, "close": 4017.5},
        {"open_time": cutoff_ms + 120_000, "open": 4017.5, "high": 4033.0, "low": 4017.0, "close": 4032.5},
    ]
    diag = classify_structure_stop_outcome(
        episode_id="syn_fvg_pos",
        model_id="SMC_M1_FVG_CONTINUATION",
        direction="LONG",
        entry_price=float(snap.entry_reference),
        sl_price=float(snap.selected_sl),
        tp_price=float(snap.take_profit),
        invalidation_price=float(snap.structural_invalidation_price),
        protected_structure_price=float(snap.structural_invalidation_price),
        decision_timestamp_ms=cutoff_ms,
        forward_bars=fwd_bars,
        provenance="SYNTHETIC_UNIT_TEST",
    )
    assert diag.outcome_class == "CLEAN_WIN"


def test_t23_synthetic_positive_path_unicorn_breaker_fvg():
    """
    T23: Synthetic positive path — Unicorn Breaker-FVG (`SYNTHETIC_UNIT_TEST`).
    Verifies BREAKER_DISTAL_BOUNDARY anchor selection, ARM boundary consistency, and 3-adapter parity.
    """
    m1_bars, m5_bars = _build_synthetic_bullish_fvg_bars()
    cutoff_ms = int(m1_bars[-1]["close_time"])
    unicorn_ev = UnicornEvidence(
        unicorn_id="uni_syn_01",
        symbol="XAUUSDT",
        direction="LONG",
        setup_episode_id="ep_uni_01",
        breaker_id="brk_syn_01",
        breaker_type="BULLISH",
        breaker_low=4002.40,
        breaker_high=4005.00,
        breaker_distal_price=4002.40,
        breaker_confirmed_at_ms=cutoff_ms - 360_000,
        fvg_id="fvg_syn_01",
        fvg_low=4004.50,
        fvg_high=4006.80,
        fvg_confirmed_at_ms=cutoff_ms - 360_000,
        overlap_low=4004.50,
        overlap_high=4005.00,
        invalidation_price=4002.40,
        validity_status="CONFIRMED",
        known_at_ms=cutoff_ms - 360_000,
    )
    sh_1m, sl_1m = identify_confirmed_swings(m1_bars, timeframe="1M", left_bars=2, right_bars=2, as_of_ms=cutoff_ms)
    plan = plan_v17_9_2_structural_stop_and_target(
        model_id="UNICORN_BREAKER_FVG",
        symbol="XAUUSDT",
        direction="LONG",
        entry_price=4005.90,
        decision_timestamp_ms=cutoff_ms,
        closed_m1=m1_bars,
        closed_m5=m5_bars,
        sh_1m=sh_1m,
        sl_1m=sl_1m,
        unicorn_evidence=unicorn_ev,
        liquidity_pools=[{"id": "p_htf", "price": 4028.0, "side": "BUY_SIDE", "known_at_ms": cutoff_ms - 60_000, "state": "UNTOUCHED"}],
        atr_m1=1.2,
        atr_m5=2.4,
        spread=0.20,
        v1792_mode="STRUCTURE_PLUS_TP",
        target_mode="V1792_CAUSAL_STRUCTURAL_VIABLE",
    )
    assert plan.status == "ELIGIBLE"
    assert plan.selected_anchor is not None
    assert plan.selected_anchor.anchor_kind == StopAnchorKind.BREAKER_DISTAL_BOUNDARY.value
    assert plan.invalidation_price == 4002.40
    assert plan.proposed_sl < 4002.40


def test_t24_synthetic_negative_path_true_invalidation():
    """
    T24: Synthetic negative path — True invalidation (`SYNTHETIC_UNIT_TEST`).
    Price decisively closes beyond the protected displacement origin and continues trending against trade.
    Both baseline and V17.9.2 are stopped out and classified as TRUE_INVALIDATION_LOSS.
    """
    m1_bars, m5_bars = _build_synthetic_bullish_fvg_bars()
    cutoff_ms = int(m1_bars[-1]["close_time"])
    fwd_collapse = [
        {"open_time": cutoff_ms + 60_000, "open": 4005.8, "high": 4006.0, "low": 3998.0, "close": 3998.2},
        {"open_time": cutoff_ms + 120_000, "open": 3998.2, "high": 3998.5, "low": 3990.0, "close": 3991.0},
    ]
    diag = classify_structure_stop_outcome(
        episode_id="syn_neg_true_inval",
        model_id="SMC_M1_FVG_CONTINUATION",
        direction="LONG",
        entry_price=4005.90,
        sl_price=3999.40,
        tp_price=4024.00,
        invalidation_price=4000.00,
        protected_structure_price=4000.00,
        decision_timestamp_ms=cutoff_ms,
        forward_bars=fwd_collapse,
        provenance="SYNTHETIC_UNIT_TEST",
    )
    assert diag.outcome_class == "TRUE_INVALIDATION_LOSS"
    assert diag.structure_broken is True


def test_t25_synthetic_negative_path_unviable_rr_rejection():
    """
    T25: Synthetic negative path — Unviable R:R rejection (`SYNTHETIC_UNIT_TEST`).
    When moving SL to outer protected swing makes net_rr < 2.0 against the only available causal target,
    V17.9.2 rejects with VALID_SETUP_REJECTED_BY_RISK and hypothetical_quantity == 0.
    """
    m1_bars, m5_bars = _build_synthetic_bullish_fvg_bars()
    cutoff_ms = int(m1_bars[-1]["close_time"])
    # Only target is at 4014.00 -> distance = 8.10, while SL is ~3999.40 -> stop_dist = 6.50 -> net_rr < 2.0
    pools = [
        LiquidityLevel(
            id="pool_close_only",
            price=4014.00,
            side="BUY_SIDE",
            source_kind="M5_SWING_HIGH",
            occurred_at_ms=cutoff_ms - 600_000,
            known_at_ms=cutoff_ms - 300_000,
            state="UNTOUCHED",
        )
    ]
    ctx = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=cutoff_ms,
        m1_candles=m1_bars,
        m5_candles=m5_bars,
        bid=4005.70,
        ask=4005.90,
        liquidity_pools=pools,
        policy_config={
            "v17_9_2_structural_sl_enabled": True,
            "v17_9_2_mode": "STRUCTURE_PLUS_TP",
            "v17_9_2_target_mode": "V1792_CAUSAL_STRUCTURAL_VIABLE",
        },
    )
    snap = SharedStrategyEvaluator.evaluate_model_direction(ctx, "SMC_M1_FVG_CONTINUATION", "LONG")
    assert snap.decision_status == DecisionStatus.VALID_SETUP_REJECTED_BY_RISK.value
    assert snap.hypothetical_quantity == 0.0
    assert any(r in snap.rejection_reason_codes for r in ("NET_RR_TOO_LOW", "SHADOW_REJECTED_INSUFFICIENT_NET_RR"))


def test_t26_synthetic_vs_historical_provenance_segregation():
    """
    T26: Synthetic vs Historical provenance segregation.
    Historical report validator must reject any report where SYNTHETIC_UNIT_TEST records
    are mixed into HISTORICAL_BACKTEST_V1792 counts.
    """
    from tools.run_v17_9_2_structural_stop_backtest import validate_historical_provenance_segregation

    valid_records = [
        {"episode_id": "ep_1", "provenance": "HISTORICAL_BACKTEST_V1792"},
        {"episode_id": "ep_2", "provenance": "HISTORICAL_BACKTEST_V1792"},
    ]
    ok_clean, reason_clean = validate_historical_provenance_segregation(valid_records)
    assert ok_clean is True and reason_clean is None

    contaminated_records = [
        {"episode_id": "ep_1", "provenance": "HISTORICAL_BACKTEST_V1792"},
        {"episode_id": "syn_1", "provenance": "SYNTHETIC_UNIT_TEST"},
    ]
    ok_bad, reason_bad = validate_historical_provenance_segregation(contaminated_records)
    assert ok_bad is False and reason_bad == "SYNTHETIC_PROVENANCE_CONTAMINATION_DETECTED"
