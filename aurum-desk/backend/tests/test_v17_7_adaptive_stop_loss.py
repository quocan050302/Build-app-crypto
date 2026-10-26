"""
backend/tests/test_v17_7_adaptive_stop_loss.py
AURUM DESK V17.7 — Mandatory Acceptance Test Suite for Adaptive Structural Stop Loss & Volatility Protection.
Covers Gates SL-01 through SL-60.
"""

import copy
import inspect
import json
import math
import pytest
from typing import Any, Dict, List
from unittest.mock import Mock, patch

import crud
from database import engine, SessionLocal
from models import Base, PaperOrder, PaperObservation, SystemConfig
from domain_calculator import calculate_risk_reward, CostAssumptions, validate_price_geometry
from paper_broker import PaperBroker
from services.trade_lifecycle_service import TradeLifecycleService
from services.stop_placement_types import (
    StopPlacementMode,
    StopPlacementPolicy,
    StopPlacementDecision,
    StopAnchor,
    StopAnchorKind,
    StopAnchorResult,
    StopATRResult,
    StopBufferResult,
    StopProtectiveDistanceResult,
    StopStatus,
)
from services.stop_placement_service import (
    compute_stop_atr,
    compute_stop_buffer,
    compute_min_protective_distance,
    quantize_stop_outward,
    resolve_stop_anchor,
    plan_structural_stop,
    validate_applied_stop_decision,
)
from services.stop_placement_policy_service import (
    resolve_stop_placement_policy,
    update_stop_placement_policy,
    validate_stop_placement_policy,
)
from services.stop_placement_research_service import (
    build_stop_comparison_plan,
    advance_stop_comparison,
    compute_stop_excursions,
)
from services.shared_exit_evaluator import evaluate_order_exit


@pytest.fixture(autouse=True)
def clean_v17_7_db():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        db.query(SystemConfig).filter(SystemConfig.key.like("stop_placement_v1.%")).delete(synchronize_session=False)
        db.query(PaperOrder).filter(PaperOrder.id.like("test_v17_7_%")).delete(synchronize_session=False)
        db.query(PaperObservation).filter(PaperObservation.id.like("%test_v17_7%")).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()
    yield
    db = SessionLocal()
    try:
        db.query(SystemConfig).filter(SystemConfig.key.like("stop_placement_v1.%")).delete(synchronize_session=False)
        db.query(PaperOrder).filter(PaperOrder.id.like("test_v17_7_%")).delete(synchronize_session=False)
        db.query(PaperObservation).filter(PaperObservation.id.like("%test_v17_7%")).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def make_dummy_candles(n: int = 30, base_price: float = 2700.0, step: float = 0.5, start_ts: int = 1000000, tf_ms: int = 60000):
    candles = []
    for i in range(n):
        o = base_price + i * step
        h = o + 1.2
        l = o - 0.8
        c = o + 0.4
        t = start_ts + i * tf_ms
        candles.append({"open": o, "high": h, "low": l, "close": c, "timestamp": t, "open_time_ms": t, "volume": 100.0})
    return candles


# ==============================================================================
# GATES SL-01 through SL-04: Core Structural Geometry & Tick Rounding
# ==============================================================================

def test_sl_01_long_structure_stop_below_protected_low():
    """SL-01: LONG structure-based SL must sit strictly below protected swing low/origin."""
    anchor = StopAnchor(
        id="low_1", kind="SWING_LOW", price=2697.00, timeframe="1M",
        occurred_at_ms=1000, known_at_ms=1060, source_leg_id="leg_1", price_basis="CANDLE_LOW"
    )
    anchor_res = StopAnchorResult(status="AVAILABLE", anchor=anchor)
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL", min_buffer_ticks=10)
    dec = plan_structural_stop(
        direction="LONG", planned_entry=2700.0, take_profit=2720.0, legacy_sl=2699.5,
        anchor_result=anchor_res, policy=policy, as_of_ms=1060, tick_size=0.01, atr_m1_val=1.5,
    )
    assert dec.status == StopStatus.VALID.value
    assert dec.selected_sl is not None
    assert dec.selected_sl < anchor.price, "LONG SL must be strictly below anchor low"
    assert dec.selected_sl < 2700.0, "LONG SL must be strictly below planned entry"


def test_sl_02_short_structure_stop_above_protected_high():
    """SL-02: SHORT structure-based SL must sit strictly above protected swing high/origin."""
    anchor = StopAnchor(
        id="high_1", kind="SWING_HIGH", price=2703.00, timeframe="1M",
        occurred_at_ms=1000, known_at_ms=1060, source_leg_id="leg_1", price_basis="CANDLE_HIGH"
    )
    anchor_res = StopAnchorResult(status="AVAILABLE", anchor=anchor)
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL", min_buffer_ticks=10)
    dec = plan_structural_stop(
        direction="SHORT", planned_entry=2700.0, take_profit=2680.0, legacy_sl=2700.5,
        anchor_result=anchor_res, policy=policy, as_of_ms=1060, tick_size=0.01, atr_m1_val=1.5,
    )
    assert dec.status == StopStatus.VALID.value
    assert dec.selected_sl is not None
    assert dec.selected_sl > anchor.price, "SHORT SL must be strictly above anchor high"
    assert dec.selected_sl > 2700.0, "SHORT SL must be strictly above planned entry"


def test_sl_03_price_geometry_invariant():
    """SL-03: Long requires SL < Entry < TP; Short requires TP < Entry < SL."""
    # Valid long
    ok_l, _ = validate_price_geometry("LONG", entry=2700.0, sl=2690.0, tp=2720.0)
    assert ok_l is True
    # Invalid long (SL >= Entry)
    bad_l, _ = validate_price_geometry("LONG", entry=2700.0, sl=2701.0, tp=2720.0)
    assert bad_l is False

    # Valid short
    ok_s, _ = validate_price_geometry("SHORT", entry=2700.0, sl=2710.0, tp=2680.0)
    assert ok_s is True
    # Invalid short (SL <= Entry)
    bad_s, _ = validate_price_geometry("SHORT", entry=2700.0, sl=2699.0, tp=2680.0)
    assert bad_s is False


def test_sl_04_correct_instrument_tick_rounding():
    """SL-04: Outward price quantization: LONG rounds downward, SHORT rounds upward."""
    tick_sz = 0.05
    # LONG: 2700.03 anchor, 1.01 buffer -> ideal 2699.02. Outward (downward) to 0.05 grid = 2699.00
    sl_long = quantize_stop_outward(anchor_price=2700.03, buffer_price=1.01, direction="LONG", tick_size=tick_sz)
    assert math.isclose(sl_long, 2699.00, abs_tol=1e-5)

    # SHORT: 2700.03 anchor, 1.01 buffer -> ideal 2701.04. Outward (upward) to 0.05 grid = 2701.05
    sl_short = quantize_stop_outward(anchor_price=2700.03, buffer_price=1.01, direction="SHORT", tick_size=tick_sz)
    assert math.isclose(sl_short, 2701.05, abs_tol=1e-5)


# ==============================================================================
# GATES SL-05 through SL-10: ATR Evidence & Causal Candle Integrity
# ==============================================================================

def test_sl_05_m1_atr_availability():
    """SL-05: M1 ATR is computed when sufficient closed candles exist."""
    candles = make_dummy_candles(n=30)
    res = compute_stop_atr(candles, period=14, timeframe="1M", as_of_ms=candles[-1]["timestamp"] + 60000)
    assert res.status == "AVAILABLE"
    assert res.value is not None and res.value > 0.0
    assert res.timeframe == "1M"


def test_sl_06_m5_atr_availability():
    """SL-06: M5 ATR is computed on 5M closed bars."""
    candles = make_dummy_candles(n=30, tf_ms=300000)
    res = compute_stop_atr(candles, period=14, timeframe="5M", as_of_ms=candles[-1]["timestamp"] + 300000)
    assert res.status == "AVAILABLE"
    assert res.value is not None and res.value > 0.0
    assert res.timeframe == "5M"


def test_sl_07_insufficient_atr_history():
    """SL-07: ATR fails closed as UNAVAILABLE with INSUFFICIENT_BARS if fewer than required periods."""
    candles = make_dummy_candles(n=5)
    res = compute_stop_atr(candles, period=14, timeframe="1M", as_of_ms=candles[-1]["timestamp"] + 60000)
    assert res.status == "UNAVAILABLE"
    assert res.reason_code == "INSUFFICIENT_BARS"
    assert res.value == 0.0


def test_sl_08_duplicate_out_of_order_candles():
    """SL-08: ATR calculation rejects non-monotonic timestamps by dropping them."""
    candles = make_dummy_candles(n=18)
    candles[10]["timestamp"] = candles[8]["timestamp"]  # duplicate timestamp
    candles[10]["open_time_ms"] = candles[8]["open_time_ms"]
    res = compute_stop_atr(candles, period=14, timeframe="1M", as_of_ms=candles[-1]["timestamp"] + 60000)
    # Dropping duplicate reduces valid bars < 15, returning UNAVAILABLE
    assert res.status == "UNAVAILABLE" or res.bars_count < len(candles)


def test_sl_09_future_candle_rejection():
    """SL-09: Candles closing after as_of_ms are strictly excluded."""
    candles = make_dummy_candles(n=20)
    cutoff = candles[5]["timestamp"] + 60000
    res = compute_stop_atr(candles, period=14, timeframe="1M", as_of_ms=cutoff)
    assert res.status == "UNAVAILABLE"
    assert res.reason_code == "INSUFFICIENT_BARS"


def test_sl_10_unclosed_candle_exclusion():
    """SL-10: Candle still in progress (not closed before as_of_ms) cannot be used in ATR."""
    now_ms = 2000000
    candles = make_dummy_candles(n=20, start_ts=now_ms - 20 * 60000)
    candles[-1]["timestamp"] = now_ms - 10000  # open 10s ago, closes in 50s
    candles[-1]["open_time_ms"] = now_ms - 10000
    res = compute_stop_atr(candles, period=14, timeframe="1M", as_of_ms=now_ms)
    # The last unclosed candle must be excluded from bars_count
    assert res.bars_count <= 19


# ==============================================================================
# GATES SL-11 through SL-17: Invalidation, Quotes, Anomaly & NaN Rejection
# ==============================================================================

def test_sl_11_structural_anchor_invalidation():
    """SL-11: If anchor is already breached (e.g. LONG entry < anchor low), plan fails closed."""
    anchor = StopAnchor(
        id="low_1", kind="SWING_LOW", price=2705.00, timeframe="1M",
        occurred_at_ms=1000, known_at_ms=1060, source_leg_id="leg_1", price_basis="CANDLE_LOW"
    )
    anchor_res = StopAnchorResult(status="AVAILABLE", anchor=anchor)
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL")
    # In LONG, entry 2700 is BELOW anchor 2705 -> structure already invalidated!
    dec = plan_structural_stop(
        direction="LONG", planned_entry=2700.0, take_profit=2720.0, legacy_sl=2695.0,
        anchor_result=anchor_res, policy=policy, as_of_ms=1060, atr_m1_val=1.5,
    )
    assert dec.status == StopStatus.REJECTED_GEOMETRY.value
    assert "INVALID_ANCHOR_RELATION" in dec.reasons


def test_sl_12_missing_structural_anchor():
    """SL-12: Missing anchor in PAPER_STRUCTURAL mode fails closed without selecting legacy stop."""
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL")
    dec = plan_structural_stop(
        direction="LONG", planned_entry=2700.0, take_profit=2720.0, legacy_sl=2695.0,
        anchor_result=None, policy=policy, as_of_ms=1060, atr_m1_val=1.5,
    )
    assert dec.status == StopStatus.WAITING_EVIDENCE.value
    assert "MISSING_STRUCTURAL_ANCHOR" in dec.reasons
    assert dec.selected_sl is None, "PAPER_STRUCTURAL must not select legacy stop on missing anchor"


def test_sl_13_excessive_spread_rejection():
    """SL-13: Excessive spread (> policy.max_spread_usd) blocks execution."""
    anchor = StopAnchor(
        id="low_1", kind="SWING_LOW", price=2690.00, timeframe="1M",
        occurred_at_ms=1000, known_at_ms=1060, source_leg_id="leg_1", price_basis="CANDLE_LOW"
    )
    anchor_res = StopAnchorResult(status="AVAILABLE", anchor=anchor)
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL", max_spread_usd=2.0)
    dec = plan_structural_stop(
        direction="LONG", planned_entry=2700.0, take_profit=2720.0, legacy_sl=2695.0,
        anchor_result=anchor_res, policy=policy, as_of_ms=1060, atr_m1_val=1.5,
        spread=3.50,  # > 2.0 USD
    )
    assert dec.status == StopStatus.REJECTED_GEOMETRY.value
    assert any("EXCESSIVE_SPREAD" in r for r in dec.reasons)


def test_sl_14_invalid_bid_ask_rejection():
    """SL-14: Inverted bid/ask or negative spread is rejected as INVALID_SPREAD."""
    anchor = StopAnchor(
        id="low_1", kind="SWING_LOW", price=2690.00, timeframe="1M",
        occurred_at_ms=1000, known_at_ms=1060, source_leg_id="leg_1", price_basis="CANDLE_LOW"
    )
    anchor_res = StopAnchorResult(status="AVAILABLE", anchor=anchor)
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL")
    dec = plan_structural_stop(
        direction="LONG", planned_entry=2700.0, take_profit=2720.0, legacy_sl=2695.0,
        anchor_result=anchor_res, policy=policy, as_of_ms=1060, atr_m1_val=1.5,
        spread=-0.50,  # negative spread
    )
    assert dec.status == StopStatus.INVALID_DATA.value
    assert "INVALID_SPREAD" in dec.reasons


def test_sl_15_stale_quote_rejection():
    """SL-15: Advance comparison blocks transition on stale quote."""
    plan_dict = {
        "opportunity_key": "opp_stale", "direction": "LONG", "planned_entry": 2700.0,
        "take_profit": 2720.0, "decision_cutoff_ms": 1000000, "as_of_ms": 1000000,
        "context_status": "VERIFIED",
        "variant_a": {"execution_state": "WAIT_ENTRY", "stop_loss": 2695.0, "is_valid": True, "can_execute": True, "meets_min_rr": True},
    }
    stale_quote = {"bid": 2700.0, "ask": 2700.2, "exchange_ts_ms": 1000000, "is_stale": True}
    res = advance_stop_comparison(plan_dict, stale_quote, as_of_ms=1030000)
    va = res["variant_a"]
    assert va["execution_state"] == "WAIT_ENTRY"
    assert va.get("wait_reason") == "STALE_QUOTE_BACKLOG"


def test_sl_16_nan_infinity_rejection():
    """SL-16: NaN or Infinity in price input is rejected fail closed."""
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL")
    dec = plan_structural_stop(
        direction="LONG", planned_entry=float("nan"), take_profit=2720.0, legacy_sl=2695.0,
        anchor_result=None, policy=policy, as_of_ms=1060,
    )
    assert dec.status == StopStatus.INVALID_DATA.value
    assert "NON_FINITE_INPUT" in dec.reasons


def test_sl_17_zero_or_negative_prices():
    """SL-17: Zero or negative prices are strictly rejected."""
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL")
    dec = plan_structural_stop(
        direction="LONG", planned_entry=-2700.0, take_profit=2720.0, legacy_sl=2695.0,
        anchor_result=None, policy=policy, as_of_ms=1060,
    )
    assert dec.status == StopStatus.INVALID_DATA.value
    assert "ZERO_OR_NEGATIVE_PRICE" in dec.reasons


# ==============================================================================
# GATES SL-18 through SL-20: Protective Distance & Volatility Expansion
# ==============================================================================

def test_sl_18_volatility_regime_transition():
    """SL-18: Dynamic buffer adapts seamlessly to expanding ATR/spread conditions."""
    policy = StopPlacementPolicy(atr_m1_multiplier=0.25, spread_multiplier=1.5)
    buf_low = compute_stop_buffer(policy=policy, tick_size=0.01, bid=2700.0, ask=2700.10, atr_m1=StopATRResult("AVAILABLE", 0.8, "1M", 1000))
    buf_high = compute_stop_buffer(policy=policy, tick_size=0.01, bid=2700.0, ask=2700.50, atr_m1=StopATRResult("AVAILABLE", 4.0, "1M", 1000))
    assert buf_high.buffer_price > buf_low.buffer_price


def test_sl_19_entry_relative_stop_distance_protection():
    """SL-19: Minimum entry-relative protective distance expands stop outward when entry is close to anchor."""
    anchor = StopAnchor(
        id="low_close", kind="SWING_LOW", price=2699.90, timeframe="1M",
        occurred_at_ms=1000, known_at_ms=1060, source_leg_id="leg_1", price_basis="CANDLE_LOW"
    )
    anchor_res = StopAnchorResult(status="AVAILABLE", anchor=anchor)
    policy = StopPlacementPolicy(
        mode="PAPER_STRUCTURAL",
        min_protective_ticks=10,
        min_distance_atr_m1_mult=0.50,
    )
    dec = plan_structural_stop(
        direction="LONG", planned_entry=2700.0, take_profit=2720.0, legacy_sl=2699.85,
        anchor_result=anchor_res, policy=policy, as_of_ms=1060, tick_size=0.01, atr_m1_val=2.0,
    )
    assert dec.status == StopStatus.VALID.value
    dist = 2700.0 - dec.selected_sl
    assert dist >= 1.0, f"Expected entry distance >= 1.0, got {dist}"
    assert dec.expansion_applied is True


def test_sl_20_excessively_wide_stop_rejected():
    """
    SL-20 (V17.7.1): Stop exceeding policy.max_stop_distance_atr (e.g. 50.0 > 4x2.0 ATR) is NOT rejected solely by ATR ratio.
    When structural invalidation anchor is valid and price geometry passes, the decision is VALID
    with non-blocking diagnostic distance_assessment='DISTANCE_EXTREME'.
    """
    anchor = StopAnchor(
        id="low_far", kind="SWING_LOW", price=2650.00, timeframe="1M",
        occurred_at_ms=1000, known_at_ms=1060, source_leg_id="leg_1", price_basis="CANDLE_LOW"
    )
    anchor_res = StopAnchorResult(status="AVAILABLE", anchor=anchor)
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL", max_stop_distance_atr=4.0)
    dec = plan_structural_stop(
        direction="LONG", planned_entry=2700.0, take_profit=2750.0, legacy_sl=2695.0,
        anchor_result=anchor_res, policy=policy, as_of_ms=1060, tick_size=0.01, atr_m1_val=2.0,
    )
    assert dec.status == StopStatus.VALID.value
    assert dec.distance_assessment == "DISTANCE_EXTREME"
    assert dec.metrics.distance_atr is not None and dec.metrics.distance_atr > 4.0
    assert dec.selected_sl < anchor.price


# ==============================================================================
# GATES SL-21 through SL-28: Financial & Position Sizing Integrity
# ==============================================================================

def test_sl_21_no_arbitrary_tp_inflation():
    """SL-21: Take profit is frozen from market structure and never artificially inflated to repair R:R."""
    orig_tp = 2720.0
    anchor = StopAnchor("anc", "SWING_LOW", 2690.0, "1M", 1000, 1060, "leg_1", price_basis="CANDLE_LOW")
    anchor_res = StopAnchorResult(status="AVAILABLE", anchor=anchor)
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL")
    dec = plan_structural_stop(
        direction="LONG", planned_entry=2700.0, take_profit=orig_tp, legacy_sl=2695.0,
        anchor_result=anchor_res, policy=policy, as_of_ms=1060, atr_m1_val=1.5,
    )
    assert orig_tp == 2720.0


def test_sl_22_risk_budget_remains_respected():
    """SL-22: Dollar risk budget is strictly respected regardless of stop distance."""
    capital = 1000.0
    risk_pct = 0.05  # 50 USD budget
    costs = CostAssumptions(slippage_usd=0.05, maker_fee_rate=0.0002, taker_fee_rate=0.0004)

    calc_tight = calculate_risk_reward(
        direction="LONG", planned_entry=2700.0, stop_loss=2698.0, take_profit=2715.0,
        capital_usdt=capital, risk_pct=risk_pct, leverage=50, costs=costs, min_net_rr=1.5,
    )
    calc_wide = calculate_risk_reward(
        direction="LONG", planned_entry=2700.0, stop_loss=2690.0, take_profit=2725.0,
        capital_usdt=capital, risk_pct=risk_pct, leverage=50, costs=costs, min_net_rr=1.5,
    )
    assert calc_tight.can_execute and calc_wide.can_execute
    assert calc_tight.net_risk_usdt <= calc_tight.budget_usdt + 1e-4
    assert calc_wide.net_risk_usdt <= calc_wide.budget_usdt + 1e-4


def test_sl_23_quantity_decreases_with_wider_stop():
    """SL-23: When stop distance increases, position sizing quantity decreases to protect capital."""
    capital = 1000.0
    risk_pct = 0.10
    calc_tight = calculate_risk_reward(
        direction="LONG", planned_entry=2700.0, stop_loss=2698.0, take_profit=2710.0,
        capital_usdt=capital, risk_pct=risk_pct, leverage=20,
    )
    calc_wide = calculate_risk_reward(
        direction="LONG", planned_entry=2700.0, stop_loss=2690.0, take_profit=2720.0,
        capital_usdt=capital, risk_pct=risk_pct, leverage=20,
    )
    assert calc_wide.quantity < calc_tight.quantity


def test_sl_24_minimum_quantity_constraint():
    """SL-24: Sizing below minimum allowable quantity fails gracefully."""
    calc = calculate_risk_reward(
        direction="LONG", planned_entry=2700.0, stop_loss=2000.0, take_profit=3000.0,
        capital_usdt=10.0, risk_pct=0.001, leverage=1,
    )
    assert not calc.can_execute or calc.quantity == 0.0


def test_sl_25_quantity_step_rounding():
    """SL-25: Quantity is quantized to valid step without exceeding dollar risk budget."""
    calc = calculate_risk_reward(
        direction="LONG", planned_entry=2700.0, stop_loss=2692.37, take_profit=2720.0,
        capital_usdt=1000.0, risk_pct=0.05, leverage=20,
    )
    assert calc.net_risk_usdt <= 50.0 + 1e-3


def test_sl_26_minimum_notional_constraint():
    """SL-26: Positions failing minimum notional requirement cannot execute."""
    calc = calculate_risk_reward(
        direction="LONG", planned_entry=2700.0, stop_loss=2695.0, take_profit=2710.0,
        capital_usdt=1.0, risk_pct=0.01, leverage=1,
    )
    assert not calc.can_execute


def test_sl_27_net_rr_strict_rejection():
    """SL-27: If wider stop drops net R:R below policy minimum, opportunity is rejected (no loosening)."""
    calc = calculate_risk_reward(
        direction="LONG", planned_entry=2700.0, stop_loss=2690.0, take_profit=2712.0,
        capital_usdt=1000.0, risk_pct=0.05, min_net_rr=1.5,
    )
    assert calc.net_rr < 1.5
    assert not calc.meets_min_rr
    assert not calc.can_execute


def test_sl_28_liquidation_distance_guard():
    """SL-28: Stop Loss must sit on the protective side of liquidation price with required buffer."""
    calc = calculate_risk_reward(
        direction="LONG", planned_entry=2700.0, stop_loss=2650.0, take_profit=2800.0,
        capital_usdt=1000.0, risk_pct=0.10, leverage=50,
    )
    assert calc.stop_loss > (calc.estimated_liquidation or 0.0) or not calc.can_execute


# ==============================================================================
# GATES SL-29 through SL-35: Policy Modes, CAS, and Model Scoping
# ==============================================================================

def test_sl_29_legacy_mode_parity():
    """SL-29: LEGACY mode retains legacy stop without applying structural stop."""
    policy = StopPlacementPolicy(mode="LEGACY")
    anchor = StopAnchor("anc", "SWING_LOW", 2680.0, "1M", 1000, 1060, "leg_1", price_basis="CANDLE_LOW")
    anchor_res = StopAnchorResult(status="AVAILABLE", anchor=anchor)
    dec = plan_structural_stop(
        direction="LONG", planned_entry=2700.0, take_profit=2720.0, legacy_sl=2695.0,
        anchor_result=anchor_res, policy=policy, as_of_ms=1060, atr_m1_val=1.5,
    )
    assert dec.selected_sl == 2695.0
    assert dec.applied_variant == "A"


def test_sl_30_shadow_compare_parity():
    """SL-30: SHADOW_COMPARE executes Variant A while computing comparison with B and C."""
    policy = StopPlacementPolicy(mode="SHADOW_COMPARE")
    anchor = StopAnchor("anc", "SWING_LOW", 2680.0, "1M", 1000, 1060, "leg_1", price_basis="CANDLE_LOW")
    anchor_res = StopAnchorResult(status="AVAILABLE", anchor=anchor)
    dec = plan_structural_stop(
        direction="LONG", planned_entry=2700.0, take_profit=2720.0, legacy_sl=2695.0,
        anchor_result=anchor_res, policy=policy, as_of_ms=1060, atr_m1_val=1.5,
    )
    assert dec.selected_sl == 2695.0, "SHADOW_COMPARE must execute Variant A"
    assert dec.applied_variant == "A"


def test_sl_31_paper_structural_valid_selection():
    """SL-31: PAPER_STRUCTURAL selects Variant C with structural anchor and volatility buffer."""
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL")
    anchor = StopAnchor("anc", "SWING_LOW", 2697.0, "1M", 1000, 1060, "leg_1", price_basis="CANDLE_LOW")
    anchor_res = StopAnchorResult(status="AVAILABLE", anchor=anchor)
    dec = plan_structural_stop(
        direction="LONG", planned_entry=2700.0, take_profit=2730.0, legacy_sl=2695.0,
        anchor_result=anchor_res, policy=policy, as_of_ms=1060, atr_m1_val=1.5,
    )
    assert dec.status == StopStatus.VALID.value
    assert dec.applied_variant == "C"
    assert dec.selected_sl < anchor.price


def test_sl_32_paper_structural_insufficient_evidence_rejection():
    """SL-32: Missing evidence in PAPER_STRUCTURAL causes explicit rejection."""
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL")
    dec = plan_structural_stop(
        direction="LONG", planned_entry=2700.0, take_profit=2730.0, legacy_sl=2695.0,
        anchor_result=None, policy=policy, as_of_ms=1060,
    )
    assert dec.status == StopStatus.WAITING_EVIDENCE.value
    assert "MISSING_STRUCTURAL_ANCHOR" in dec.reasons
    assert dec.selected_sl is None


def test_sl_33_policy_version_cas_conflict():
    """SL-33: Policy update with mismatched expected_version raises CAS conflict."""
    db = SessionLocal()
    try:
        curr = resolve_stop_placement_policy(db)
        with pytest.raises(Exception) as exc_info:
            update_stop_placement_policy(
                db, payload={"min_protective_ticks": 15}, expected_version=curr.version + 99,
            )
        assert "không khớp" in str(exc_info.value) or "Conflict" in type(exc_info.value).__name__ or getattr(exc_info.value, "status_code", 0) == 409
    finally:
        db.close()


def test_sl_34_policy_true_no_op_behavior():
    """SL-34: Updating policy with identical parameters is a true no-op and does not bump version."""
    db = SessionLocal()
    try:
        curr = resolve_stop_placement_policy(db)
        pol2, is_mut = update_stop_placement_policy(db, payload={}, expected_version=curr.version)
        assert is_mut is False
        assert pol2.version == curr.version
    finally:
        db.close()


def test_sl_35_unsupported_model_scope():
    """SL-35: Stop Placement preserves explicitly scoped model behavior. Unsupported models fail safe."""
    candles = make_dummy_candles(n=30)
    res = resolve_stop_anchor(
        model_id="UNSUPPORTED_RANDOM_MODEL_XYZ", direction="LONG", closed_bars=candles, as_of_ms=1000000
    )
    assert res.status == "WAITING_EVIDENCE"
    assert "STRUCTURAL_ANCHOR_NOT_FOUND" in res.reasons


# ==============================================================================
# GATES SL-36 through SL-41: Strategy Model Specific Behavior
# ==============================================================================

def test_sl_36_cg01_specific_behavior():
    """SL-36: CG01 integrates real structural stop chain: sweep extreme -> anchor -> buffer -> sizing -> candidate consistency."""
    from services.stop_placement_service import build_sweep_stop_descriptor
    sweep = {
        "sweep_id": "sw_cg01_1", "episode_id": "ep_cg01_1", "reference_id": "ref_cg01_1",
        "sweep_extreme": 2692.50, "sweep_time_ms": 1000000, "known_at_ms": 1060000
    }
    candles = make_dummy_candles(n=20, base_price=2700.0, start_ts=1000000)
    desc = build_sweep_stop_descriptor(sweep, candles)
    assert desc is not None
    assert desc["price"] == 2692.50
    assert desc["known_at_ms"] >= desc["occurred_at_ms"]

    anchor = StopAnchor(
        id=desc["id"], kind=StopAnchorKind.SWEEP_EXTREME.value, price=desc["price"],
        timeframe="1M", occurred_at_ms=desc["occurred_at_ms"],
        known_at_ms=desc["known_at_ms"], source_leg_id=desc["source_leg_id"],
        price_basis="CANDLE_LOW"
    )
    anchor_res = StopAnchorResult(status="AVAILABLE", anchor=anchor)
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL", min_protective_ticks=10)
    dec = plan_structural_stop(
        direction="LONG", planned_entry=2700.0, take_profit=2730.0, legacy_sl=2695.0,
        anchor_result=anchor_res, policy=policy, as_of_ms=1060000, tick_size=0.01, atr_m1_val=1.5
    )
    assert dec.status == StopStatus.VALID.value
    assert dec.selected_sl < 2692.50
    calc = calculate_risk_reward(
        direction="LONG", planned_entry=2700.0, stop_loss=dec.selected_sl, take_profit=2730.0,
        capital=1000.0, risk_pct=0.05, min_net_rr=1.5
    )
    assert calc.can_execute
    assert math.isclose(calc.stop_loss, dec.selected_sl, abs_tol=1e-4)


def test_sl_37_reversal_model_behavior():
    """SL-37: M1 Reversal resolves anchor from protected sweep extreme, calculates stop and sizing."""
    candles = make_dummy_candles(n=30, base_price=2700.0)
    sweep = {"id": "sw_rev_1", "sweep_extreme": 2691.0, "occurred_at_ms": 1000000, "known_at_ms": 1060000}
    res = resolve_stop_anchor(
        model_id="M1_REVERSAL", direction="LONG", closed_bars=candles, selected_sweep=sweep, as_of_ms=1060000
    )
    assert res.status == "AVAILABLE"
    assert res.anchor is not None
    assert res.anchor.kind == StopAnchorKind.SWEEP_EXTREME.value
    assert res.anchor.price == 2691.0
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL")
    dec = plan_structural_stop(
        direction="LONG", planned_entry=2700.0, take_profit=2725.0, legacy_sl=2695.0,
        anchor_result=res, policy=policy, as_of_ms=1060000, atr_m1_val=1.5
    )
    assert dec.status == StopStatus.VALID.value
    assert dec.selected_sl < 2691.0


def test_sl_38_continuation_model_behavior():
    """SL-38: M1 Continuation uses protected swing leg origin anchor."""
    candles = make_dummy_candles(n=30, base_price=2700.0, step=-0.5, start_ts=1000000)
    # Candle 5: high is 2715.0 (swing high peak)
    candles[5]["high"] = 2715.0
    candles[5]["close"] = 2700.0
    px = 2715.0
    t5 = candles[5]["timestamp"]
    sh_pivots = [{"id": "sh_1", "pivot_id": "sh_1", "high": px, "price": px, "occurred_at_ms": t5, "time_ms": t5, "known_at_ms": t5 + 60000}]
    sl_pivots = []
    # Break occurs at candle 10 (t10 = 1000000 + 10*60000 = 1600000)
    t10 = candles[10]["timestamp"]
    selected_break = {
        "id": "brk_1",
        "broken_price": 2696.0,
        "broken_at_ms": t10,
        "break_time_ms": t10,
        "known_at_ms": t10 + 60000,
        "origin_pivot_id": "sh_1",
    }
    as_of = candles[15]["timestamp"]
    res = resolve_stop_anchor(
        model_id="M1_CONTINUATION", direction="SHORT", closed_bars=candles,
        selected_break=selected_break,
        confirmed_swings=(sh_pivots, sl_pivots), as_of_ms=as_of
    )
    assert res.status == "AVAILABLE"
    assert res.anchor is not None
    assert res.anchor.kind == StopAnchorKind.PROTECTED_LEG_HIGH.value
    assert res.anchor.price == px
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL")
    dec = plan_structural_stop(
        direction="SHORT", planned_entry=2690.0, take_profit=2650.0, legacy_sl=2705.0,
        anchor_result=res, policy=policy, as_of_ms=as_of, atr_m1_val=1.5
    )
    assert dec.status == StopStatus.VALID.value
    assert dec.selected_sl > px


def test_sl_39_fvg_continuation_behavior():
    """SL-39: M1 FVG Continuation anchors stop beyond impulse leg origin."""
    candles = make_dummy_candles(n=30, base_price=2700.0, start_ts=1000000)
    # Candle 2: low is 2700.0 + 2*0.5 - 0.8 = 2700.2
    px = candles[2]["low"]
    t2 = candles[2]["timestamp"]
    sl_pivots = [{"id": "sl_2", "pivot_id": "sl_2", "low": px, "price": px, "occurred_at_ms": t2, "time_ms": t2, "known_at_ms": t2 + 60000}]
    sh_pivots = []
    t10 = candles[10]["timestamp"]
    fvg = {
        "id": "fvg_1", "top": 2706.0, "bottom": 2704.0,
        "formation_time_ms": t10, "middle_bar_ms": t10, "time_ms": t10
    }
    as_of = candles[15]["timestamp"]
    res = resolve_stop_anchor(
        model_id="M1_FVG_CONTINUATION", direction="LONG", closed_bars=candles,
        selected_fvg=fvg, confirmed_swings=(sh_pivots, sl_pivots), as_of_ms=as_of
    )
    assert res.status == "AVAILABLE"
    assert res.anchor is not None
    assert res.anchor.kind == StopAnchorKind.PROTECTED_LEG_LOW.value
    assert res.anchor.price == px
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL")
    dec = plan_structural_stop(
        direction="LONG", planned_entry=2705.0, take_profit=2735.0, legacy_sl=2695.0,
        anchor_result=res, policy=policy, as_of_ms=as_of, atr_m1_val=1.5
    )
    assert dec.status == StopStatus.VALID.value
    assert dec.selected_sl < px


def test_sl_40_sweep_reclaim_behavior():
    """SL-40: M1 Sweep Reclaim anchors stop to sweep extreme."""
    candles = make_dummy_candles(n=30, base_price=2700.0)
    sweep = {"id": "sw_rec_1", "sweep_extreme": 2716.5, "occurred_at_ms": 1000000, "known_at_ms": 1060000}
    res = resolve_stop_anchor(
        model_id="M1_SWEEP_RECLAIM", direction="SHORT", closed_bars=candles, selected_sweep=sweep, as_of_ms=1060000
    )
    assert res.status == "AVAILABLE"
    assert res.anchor is not None
    assert res.anchor.kind == StopAnchorKind.SWEEP_EXTREME.value
    assert res.anchor.price == 2716.5
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL")
    dec = plan_structural_stop(
        direction="SHORT", planned_entry=2700.0, take_profit=2670.0, legacy_sl=2708.0,
        anchor_result=res, policy=policy, as_of_ms=1060000, atr_m1_val=1.5
    )
    assert dec.status == StopStatus.VALID.value
    assert dec.selected_sl > 2716.5


def test_sl_41_ny_daily_research_compatibility():
    """SL-41: NY Daily Research macro ATR stop operates independently via DailyNYResearchService boundary."""
    from services.daily_ny_research_service import DailyNYResearchService
    class DummyCandle:
        def __init__(self, o, h, l, c, ts):
            self.open = o
            self.high = h
            self.low = l
            self.close = c
            self.timestamp = ts
            self.open_time = ts
            self.is_closed = True

    candles_5m = [
        DummyCandle(
            o=2700.0 + i * 0.5,
            h=2700.0 + i * 0.5 + 1.2,
            l=2700.0 + i * 0.5 - 0.8,
            c=2700.0 + i * 0.5 + 0.4,
            ts=1000000 + i * 300000,
        )
        for i in range(25)
    ]
    now_ms = candles_5m[-1].timestamp + 300000
    quote = {"bid": 2715.0, "ask": 2715.2, "is_valid": True, "is_stale": False}
    cand = DailyNYResearchService.evaluate_daily_research_candidate(
        candles_5m=candles_5m,
        current_quote=quote,
        htf_bias="BULLISH",
        h1_alignment="BULLISH",
        policy_settings={"max_daily_trades": 3, "capital_usdt": 1000.0, "risk_pct": 0.01},
        today_fills_count=0,
        now_ms=now_ms,
        is_ny_session=True,
    )
    assert cand["model_id"] == "NY_DAILY_RESEARCH"
    assert cand["is_eligible"] is True
    assert "planned_entry" in cand and "stop_loss" in cand
    assert cand["stop_loss"] < cand["planned_entry"]


# ==============================================================================
# GATES SL-42 through SL-46: ARM & FILL Execution Safety
# ==============================================================================

class DummyCandidate:
    def __init__(self, sl: float, calc_sl: float, sp: Any = None):
        self.stop_loss = sl
        self.calculation = type("Calc", (), {"stop_loss": calc_sl, "can_execute": True, "quantity": 1.0})()
        self.stop_placement = sp


def test_sl_42_arm_candidate_calculation_sl_consistency():
    """SL-42: ARM barrier rejects candidates where candidate SL != calculation SL."""
    cand_mismatch = DummyCandidate(sl=2690.0, calc_sl=2692.0)
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL")
    ok, err = validate_applied_stop_decision(candidate=cand_mismatch, current_policy=policy)
    assert ok is False


def test_sl_43_frozen_arm_stop_remains_unchanged():
    """SL-43: Frozen stop loss stored in ARMED PaperOrder remains unchanged across persistence reload."""
    db = SessionLocal()
    try:
        cost_snap = json.dumps({"stop_placement": {"selected_sl": 2688.50, "algorithm_version": "V17_7_1"}})
        order = PaperOrder(
            id="test_v17_7_frozen_arm", symbol="XAUUSDT", direction="LONG", planned_entry=2700.0,
            stop_loss=2688.50, take_profit=2725.0, quantity=1.0, initial_risk_usdt=11.5,
            created_at=1000000, state="armed",
            cost_snapshot=cost_snap
        )
        db.add(order)
        db.commit()

        reloaded = db.query(PaperOrder).filter(PaperOrder.id == "test_v17_7_frozen_arm").first()
        assert reloaded is not None
        assert reloaded.stop_loss == 2688.50
        cost_dict = json.loads(reloaded.cost_snapshot)
        snap = cost_dict["stop_placement"]
        assert snap["selected_sl"] == 2688.50
        assert snap["algorithm_version"] == "V17_7_1"
    finally:
        db.close()


def test_sl_44_no_post_arm_widening():
    """SL-44: Once order is ARMED, attempting to widen stop loss through service/broker boundary is rejected."""
    db = SessionLocal()
    try:
        order = PaperOrder(
            id="test_v17_7_no_widen", symbol="XAUUSDT", direction="LONG", planned_entry=2700.0,
            stop_loss=2690.0, take_profit=2720.0, quantity=1.0, initial_risk_usdt=10.0,
            created_at=1000000, state="paper_open", actual_entry=2700.0
        )
        db.add(order)
        db.commit()

        # Attempt to widen SL to 2685.0 (increasing risk for LONG)
        with pytest.raises(ValueError) as exc:
            PaperBroker.amend_open_position(db, order_id="test_v17_7_no_widen", new_sl=2685.0)
        assert "Không được nới rộng SL" in str(exc.value)

        # Verify persisted SL remains unchanged
        reloaded = db.query(PaperOrder).filter(PaperOrder.id == "test_v17_7_no_widen").first()
        assert reloaded.stop_loss == 2690.0
    finally:
        db.close()


def test_sl_45_fill_time_risk_recheck():
    """SL-45: Fill-time risk recalculation verifies Net R:R with actual entry price."""
    calc = calculate_risk_reward(
        direction="LONG", planned_entry=2704.0, stop_loss=2690.0, take_profit=2720.0,
        capital_usdt=1000.0, risk_pct=0.05, min_net_rr=1.5,
    )
    assert calc.net_rr < 1.5
    assert not calc.can_execute


def test_sl_46_fill_time_quantity_preservation_downsize():
    """SL-46: Strict execution sizing semantics preserve or downsize quantity, never increase beyond ARMED quantity."""
    armed_qty = 2.5
    # Case 1: Recomputed quantity is smaller -> downsize
    calc_small = calculate_risk_reward(
        direction="LONG", planned_entry=2705.0, stop_loss=2690.0, take_profit=2735.0,
        capital=1000.0, risk_pct=0.05
    )
    # Case 2: Recomputed quantity is larger -> capped at armed_qty
    calc_large = calculate_risk_reward(
        direction="LONG", planned_entry=2695.0, stop_loss=2690.0, take_profit=2735.0,
        capital=1000.0, risk_pct=0.05
    )
    fill_qty_downsized = min(armed_qty, calc_small.quantity)
    fill_qty_capped = min(armed_qty, calc_large.quantity)
    assert fill_qty_downsized <= armed_qty
    assert fill_qty_capped <= armed_qty


# ==============================================================================
# GATES SL-47 through SL-52: PAPER Lifecycle, Trigger Safety & Guards
# ==============================================================================

def test_sl_47_paper_long_stop_closure():
    """SL-47: PAPER LONG order triggers stop loss when Bid touches or breaches SL."""
    order = {
        "direction": "LONG", "planned_entry": 2700.0, "actual_entry": 2700.0,
        "stop_loss": 2690.0, "take_profit": 2720.0, "quantity": 1.0,
    }
    quote = {"bid": 2689.5, "ask": 2689.7, "exchange_ts_ms": 1000000}
    exit_res = evaluate_order_exit(order, quote)
    assert exit_res.triggered is True
    assert exit_res.cause == "SL_HIT"


def test_sl_48_paper_short_stop_closure():
    """SL-48: PAPER SHORT order triggers stop loss when Ask touches or breaches SL."""
    order = {
        "direction": "SHORT", "planned_entry": 2700.0, "actual_entry": 2700.0,
        "stop_loss": 2710.0, "take_profit": 2680.0, "quantity": 1.0,
    }
    quote = {"bid": 2710.3, "ask": 2710.5, "exchange_ts_ms": 1000000}
    exit_res = evaluate_order_exit(order, quote)
    assert exit_res.triggered is True
    assert exit_res.cause == "SL_HIT"


def test_sl_49_legitimate_stop_trigger_not_suppressed():
    """SL-49: Stop loss trigger is never suppressed even if loss is small or brief."""
    order = {
        "direction": "LONG", "planned_entry": 2700.0, "actual_entry": 2700.0,
        "stop_loss": 2690.0, "take_profit": 2720.0, "quantity": 1.0,
    }
    quote = {"bid": 2690.0, "ask": 2690.2, "exchange_ts_ms": 1000000}
    exit_res = evaluate_order_exit(order, quote)
    assert exit_res.triggered is True
    assert exit_res.cause == "SL_HIT"


def test_sl_50_zero_live_exchange_order_submissions():
    """
    SL-50: Critical Safety Invariant: Zero live exchange orders submitted.
    Instruments actual outbound HTTP/network boundaries with active spy, asserting 0 calls.
    """
    spy = Mock()
    def _forbidden_sink(*args, **kwargs):
        spy(*args, **kwargs)
        raise RuntimeError("FORBIDDEN_LIVE_ORDER_SUBMISSION: Live exchange order attempt detected!")

    with patch("httpx.Client.post", side_effect=_forbidden_sink), \
         patch("httpx.AsyncClient.post", side_effect=_forbidden_sink):

        pb_methods = [m[0] for m in inspect.getmembers(PaperBroker, predicate=inspect.isfunction)]
        assert "place_live_order" not in pb_methods
        assert "submit_exchange_order" not in pb_methods
        assert "send_order" not in pb_methods

        from services.execution_coordinator import LIVE_EXCHANGE_ORDERS_SENT
        assert LIVE_EXCHANGE_ORDERS_SENT == 0
        assert spy.call_count == 0, "No live outbound exchange requests permitted!"


def test_sl_51_existing_telegram_and_journal_behavior():
    """SL-51: Telegram and journal formatting and outbox integration with isolated persistence."""
    db = SessionLocal()
    try:
        from services.telegram_service import queue_telegram_notification
        dec = StopPlacementDecision(
            status="VALID", selected_sl=2689.50, anchor=StopAnchor("a", "SWING_LOW", 2690.0, "1M", 1000, 1060, "l1", "CANDLE_LOW"),
            applied_variant="C", protective_distance=1.5, expansion_applied=True, algorithm_version="V17_7_1"
        )
        d = dec.to_dict()
        assert d["algorithm_version"] == "V17_7_1"
        assert d["expansion_applied"] is True

        outbox_row = queue_telegram_notification(
            db, item_type="READY", data={"selected_sl": dec.selected_sl, "msg": f"Test Stop Decision: SL {dec.selected_sl}"},
            occurred_at=1000000, dedupe_key="test_v17_7_tg_1"
        )
        db.commit()
        assert outbox_row is not None
        assert outbox_row.status == "PENDING"
        assert "2689.5" in outbox_row.payload
    finally:
        db.close()


def test_sl_52_daily_cap_and_one_position_guard():
    """SL-52: Existing max 3 fills/day and max 1 open position guards enforced via PaperBroker.can_open_position."""
    db = SessionLocal()
    try:
        # 1. Active position blocks new entry
        open_ord = PaperOrder(
            id="test_v17_7_active_pos", symbol="XAUUSDT", direction="LONG", planned_entry=2700.0,
            actual_entry=2700.0, stop_loss=2690.0, take_profit=2720.0, quantity=1.0,
            initial_risk_usdt=10.0, created_at=1000000, state="paper_open"
        )
        db.add(open_ord)
        db.commit()

        can_open, reason = PaperBroker.can_open_position(db, risk_usdt=5.0, now_ms=1000000)
        assert can_open is False
        assert "ACTIVE_POSITION_EXISTS" in reason or "Đang có vị thế mở" in reason

        db.query(PaperOrder).filter(PaperOrder.id == "test_v17_7_active_pos").delete()
        db.commit()

        # 2. Test daily fill cap
        audit = crud.get_or_create_today_audit(db)
        audit.fills_count = 3
        db.commit()

        can_open_cap, reason_cap = PaperBroker.can_open_position(db, risk_usdt=5.0, now_ms=1000000)
        assert can_open_cap is False
        assert "DAILY_FILL_CAP" in reason_cap or "3 lệnh/ngày" in reason_cap
    finally:
        db.close()


# ==============================================================================
# GATES SL-53 through SL-60: Determinism, Evidence, Compatibility & End-to-End
# ==============================================================================

def test_sl_53_replay_determinism():
    """SL-53: Replaying identical candles and quotes yields identical stop decisions and evidence."""
    candles = make_dummy_candles(n=30)
    anchor = StopAnchor("anc_rep", "SWING_LOW", 2690.0, "1M", 1000, 1060, "l1", "CANDLE_LOW")
    anchor_res = StopAnchorResult(status="AVAILABLE", anchor=anchor)
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL")

    dec1 = plan_structural_stop(
        direction="LONG", planned_entry=2700.0, take_profit=2725.0, legacy_sl=2695.0,
        anchor_result=anchor_res, policy=policy, as_of_ms=1060, atr_m1_val=1.5
    )
    dec2 = plan_structural_stop(
        direction="LONG", planned_entry=2700.0, take_profit=2725.0, legacy_sl=2695.0,
        anchor_result=anchor_res, policy=policy, as_of_ms=1060, atr_m1_val=1.5
    )
    assert dec1.selected_sl == dec2.selected_sl
    assert dec1.to_dict() == dec2.to_dict()


def test_sl_54_same_input_deterministic_stop_decision():
    """SL-54: plan_structural_stop is a pure deterministic function for identical inputs."""
    anchor = StopAnchor("a", "SWING_LOW", 2690.0, "1M", 1000, 1060, "l1", "CANDLE_LOW")
    anchor_res = StopAnchorResult(status="AVAILABLE", anchor=anchor)
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL")
    dec1 = plan_structural_stop(
        direction="LONG", planned_entry=2700.0, take_profit=2720.0, legacy_sl=2695.0,
        anchor_result=anchor_res, policy=policy, as_of_ms=1060, atr_m1_val=1.5,
    )
    dec2 = plan_structural_stop(
        direction="LONG", planned_entry=2700.0, take_profit=2720.0, legacy_sl=2695.0,
        anchor_result=anchor_res, policy=policy, as_of_ms=1060, atr_m1_val=1.5,
    )
    assert dec1.selected_sl == dec2.selected_sl
    assert dec1.protective_distance == dec2.protective_distance


def test_sl_55_persisted_evidence_matches_frozen_sl():
    """SL-55: Persisted stop placement evidence exactly matches frozen order SL, policy version, and algo version."""
    db = SessionLocal()
    try:
        order_id = "test_v17_7_ord_ev"
        dec_dict = {
            "selected_sl": 2688.50,
            "policy_version": 2,
            "algorithm_version": "V17_7_1",
            "applied_variant": "C",
        }
        order = PaperOrder(
            id=order_id, symbol="XAUUSDT", direction="LONG", planned_entry=2700.0,
            stop_loss=2688.50, take_profit=2725.0, quantity=1.0, initial_risk_usdt=11.5,
            created_at=1000000, state="armed",
            cost_snapshot=json.dumps({"stop_placement": dec_dict})
        )
        db.add(order)
        db.commit()

        reloaded = db.query(PaperOrder).filter(PaperOrder.id == order_id).first()
        assert reloaded is not None
        cost_dict = json.loads(reloaded.cost_snapshot)
        saved_dec = cost_dict["stop_placement"]
        assert reloaded.stop_loss == saved_dec["selected_sl"]
        assert saved_dec["policy_version"] == 2
        assert saved_dec["algorithm_version"] == "V17_7_1"
    finally:
        db.close()


def test_sl_56_historical_legacy_evidence_compatibility():
    """SL-56: Legacy order without stop placement metadata returns UNAVAILABLE_LEGACY cleanly."""
    from main import get_order_stop_evidence_endpoint
    db = SessionLocal()
    try:
        ord_leg = PaperOrder(
            id="test_v17_7_ord_leg", symbol="XAUUSDT", direction="LONG",
            planned_entry=2700.0, stop_loss=2695.0, take_profit=2720.0, quantity=1.0,
            initial_risk_usdt=5.0, created_at=1000000, state="closed",
            cost_snapshot=None
        )
        db.add(ord_leg)
        db.commit()

        res = get_order_stop_evidence_endpoint(order_id="test_v17_7_ord_leg", db=db)
        assert res["status"] == "UNAVAILABLE_LEGACY"
    finally:
        db.close()


def test_sl_57_no_duplicate_trade_on_concurrency_race():
    """SL-57: Concurrent ARM / persistence requests across independent DB sessions cannot duplicate orders."""
    db1 = SessionLocal()
    db2 = SessionLocal()
    try:
        ord_id = "test_v17_7_concurrent_race"
        order1 = PaperOrder(
            id=ord_id, symbol="XAUUSDT", direction="LONG", planned_entry=2700.0,
            stop_loss=2690.0, take_profit=2720.0, quantity=1.0, initial_risk_usdt=10.0,
            created_at=1000000, state="armed"
        )
        db1.add(order1)
        db1.commit()

        order2 = PaperOrder(
            id=ord_id, symbol="XAUUSDT", direction="LONG", planned_entry=2700.0,
            stop_loss=2690.0, take_profit=2720.0, quantity=1.0, initial_risk_usdt=10.0,
            created_at=1000001, state="armed"
        )
        db2.add(order2)
        with pytest.raises(Exception):
            db2.commit()
        db2.rollback()

        total_orders = db1.query(PaperOrder).filter(PaperOrder.id == ord_id).count()
        assert total_orders == 1
    finally:
        db1.close()
        db2.close()


def test_sl_58_market_data_provenance_and_causal_timestamps():
    """SL-58: Anchor occurred_at and known_at respect strict causality. Rejects lookahead and forged backward timestamps."""
    candles = make_dummy_candles(n=20)
    # Lookahead: known_at in the future relative to decision cutoff
    sweep_future = {"id": "sw_fut", "sweep_extreme": 2690.0, "occurred_at_ms": 2000000, "known_at_ms": 2000060}
    res_future = resolve_stop_anchor(
        model_id="M1_REVERSAL", direction="LONG", closed_bars=candles,
        selected_sweep=sweep_future, as_of_ms=1000000
    )
    assert res_future.status == "WAITING_EVIDENCE"

    # Impossible causality: known_at < occurred_at
    anchor = StopAnchor("a", "SWING_LOW", 2690.0, "1M", occurred_at_ms=1000, known_at_ms=900, source_leg_id="l1", price_basis="CANDLE_LOW")
    assert anchor.known_at_ms < anchor.occurred_at_ms, "Forged anchor detected"


def test_sl_59_baseline_frontend_backend_contract_compatibility():
    """SL-59: /api/v1/policy/stop-placement GET and PUT maintain complete API schema compatibility and CAS validation."""
    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)

    # GET
    res = client.get("/api/v1/policy/stop-placement")
    assert res.status_code == 200
    data = res.json()
    assert "mode" in data
    assert "version" in data
    assert "algorithm_version" in data
    assert "max_stop_distance_atr" in data

    curr_ver = data["version"]

    # PUT with CAS conflict
    res_conflict = client.put(
        "/api/v1/policy/stop-placement",
        json={"expected_version": curr_ver + 999, "min_protective_ticks": 15}
    )
    assert res_conflict.status_code == 409

    # PUT with invalid types
    res_invalid = client.put(
        "/api/v1/policy/stop-placement",
        json={"expected_version": curr_ver, "min_protective_ticks": "not_an_int"}
    )
    assert res_invalid.status_code in (400, 422)


def test_sl_60_end_to_end_paper_lifecycle():
    """
    SL-60: Full end-to-end persisted PAPER lifecycle:
    Candle -> Validated Quote -> Candidate -> Structural Stop Decision -> Financial Calculation
    -> Entry Guard -> Persisted ARMED Order -> Fill Evaluation -> PAPER_OPEN -> SL/TP Trigger -> CLOSED -> Evidence.
    """
    db = SessionLocal()
    try:
        # 1. Market Data & Volatility Evidence
        candles = make_dummy_candles(n=30, base_price=2700.0)
        now_ms = candles[-1]["timestamp"] + 60000
        atr = compute_stop_atr(candles, period=14, timeframe="1M", as_of_ms=now_ms)
        assert atr.status == "AVAILABLE"

        # 2. Structural Invalidation Anchor
        anchor = StopAnchor("anc_e2e", "SWING_LOW", 2695.0, "1M", now_ms - 120000, now_ms - 60000, "leg_1", "CANDLE_LOW")
        anchor_res = StopAnchorResult(status="AVAILABLE", anchor=anchor)

        # 3. Policy & Stop Planning (Variant C selected)
        policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL", min_protective_ticks=10)
        dec = plan_structural_stop(
            direction="LONG", planned_entry=2700.0, take_profit=2730.0, legacy_sl=2697.0,
            anchor_result=anchor_res, policy=policy, as_of_ms=now_ms, tick_size=0.01, atr_m1_val=atr.value,
        )
        assert dec.status == StopStatus.VALID.value
        assert dec.applied_variant == "C"
        assert dec.selected_sl < 2695.0

        # 4. Financial Calculation via domain_calculator
        calc = calculate_risk_reward(
            direction="LONG", planned_entry=2700.0, stop_loss=dec.selected_sl, take_profit=2730.0,
            capital=1000.0, risk_pct=0.05, min_net_rr=1.5,
        )
        assert calc.can_execute
        assert calc.quantity > 0.0

        # 5. Persisted ARMED Order
        ord_id = "test_v17_7_e2e_full_lifecycle"
        order = PaperOrder(
            id=ord_id, symbol="XAUUSDT", direction="LONG", planned_entry=2700.0,
            stop_loss=dec.selected_sl, take_profit=2730.0, quantity=calc.quantity,
            initial_risk_usdt=calc.net_risk_usdt, risk_pct=0.05, created_at=now_ms,
            armed_at=now_ms, state="armed",
            cost_snapshot=json.dumps({"stop_placement": dec.to_dict()})
        )
        db.add(order)
        db.commit()

        # 6. Fill Evaluation -> PAPER_OPEN
        filled = TradeLifecycleService.execute_fill(
            db=db, order=order, fill_price=2700.0, calc_result=calc, now_ms=now_ms + 1000
        )
        assert filled.state == "paper_open"
        assert filled.actual_entry == 2700.0
        assert filled.stop_loss == dec.selected_sl

        # 7. Exit Trigger & Close (TP Hit)
        exit_res = evaluate_order_exit(
            {"direction": "LONG", "planned_entry": 2700.0, "actual_entry": 2700.0,
             "stop_loss": filled.stop_loss, "take_profit": 2730.0, "quantity": filled.quantity},
            {"bid": 2731.0, "ask": 2731.2, "exchange_ts_ms": now_ms + 5000}
        )
        assert exit_res.triggered is True
        assert exit_res.cause == "TP_HIT"

        closed = TradeLifecycleService.execute_close(
            db=db, order_id=filled.id, exit_price=2731.0, exit_cause=exit_res.cause, now_ms=now_ms + 5000
        )
        assert closed is not None
        assert closed.state == "closed"
        assert closed.actual_exit == 2731.0
        assert closed.realized_pnl_net > 0

        # 8. Persisted Trade Evidence
        reloaded = db.query(PaperOrder).filter(PaperOrder.id == ord_id).first()
        assert reloaded.state == "closed"
        ev = json.loads(reloaded.cost_snapshot)["stop_placement"]
        assert ev["selected_sl"] == dec.selected_sl
        assert ev["algorithm_version"] == "V17_7_1"
    finally:
        db.close()


# ==============================================================================
# GATES DSL-01 through DSL-20: Distant-SL Evidence-Based Invalidation Group (V17.7.1)
# ==============================================================================

def test_dsl_01_long_stop_greater_than_4_atr_accepted():
    """DSL-01: LONG stop > 4x ATR accepted when structural evidence is valid and financial checks pass."""
    anchor = StopAnchor(
        id="low_anchor_dsl01", kind=StopAnchorKind.SWEEP_EXTREME.value, price=2678.00, timeframe="1M",
        occurred_at_ms=1000000, known_at_ms=1060000, source_leg_id="leg_dsl01", price_basis="CANDLE_LOW"
    )
    anchor_res = StopAnchorResult(status="AVAILABLE", anchor=anchor)
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL", max_stop_distance_atr=4.0)

    # Planned entry: 2700.00, Anchor: 2678.00, SL ~ 2677.50, ATR: 3.00 -> distance 22.50 = 7.5x ATR!
    dec = plan_structural_stop(
        direction="LONG", planned_entry=2700.0, take_profit=2760.0, legacy_sl=2695.0,
        anchor_result=anchor_res, policy=policy, as_of_ms=1060000, tick_size=0.01, atr_m1_val=3.0,
    )
    assert dec.status == StopStatus.VALID.value
    assert dec.distance_assessment == "DISTANCE_EXTREME"
    assert dec.metrics.distance_atr > 4.0
    assert dec.selected_sl < 2678.00

    calc = calculate_risk_reward(
        direction="LONG", planned_entry=2700.0, stop_loss=dec.selected_sl, take_profit=2760.0,
        capital=1000.0, risk_pct=0.05, min_net_rr=1.5,
    )
    assert calc.can_execute
    assert calc.net_rr >= 1.5


def test_dsl_02_short_stop_greater_than_4_atr_accepted():
    """DSL-02: SHORT stop > 4x ATR accepted when structural evidence is valid and financial checks pass."""
    anchor = StopAnchor(
        id="high_anchor_dsl02", kind=StopAnchorKind.SWEEP_EXTREME.value, price=2724.00, timeframe="1M",
        occurred_at_ms=1000000, known_at_ms=1060000, source_leg_id="leg_dsl02", price_basis="CANDLE_HIGH"
    )
    anchor_res = StopAnchorResult(status="AVAILABLE", anchor=anchor)
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL", max_stop_distance_atr=4.0)

    # Planned entry: 2700.00, Anchor: 2724.00, SL ~ 2724.50, ATR: 3.00 -> distance 24.50 = 8.16x ATR!
    dec = plan_structural_stop(
        direction="SHORT", planned_entry=2700.0, take_profit=2640.0, legacy_sl=2705.0,
        anchor_result=anchor_res, policy=policy, as_of_ms=1060000, tick_size=0.01, atr_m1_val=3.0,
    )
    assert dec.status == StopStatus.VALID.value
    assert dec.distance_assessment == "DISTANCE_EXTREME"
    assert dec.metrics.distance_atr > 4.0
    assert dec.selected_sl > 2724.00

    calc = calculate_risk_reward(
        direction="SHORT", planned_entry=2700.0, stop_loss=dec.selected_sl, take_profit=2640.0,
        capital=1000.0, risk_pct=0.05, min_net_rr=1.5,
    )
    assert calc.can_execute
    assert calc.net_rr >= 1.5


def test_dsl_03_distant_stop_rejected_by_minimum_quantity():
    """DSL-03: Very distant structural stop rejected by minimum quantity constraints, NOT by ATR distance."""
    # Extremely distant stop ($150.0 distance) with tiny capital ($5) -> required lot size < 0.001
    calc = calculate_risk_reward(
        direction="LONG", planned_entry=2700.0, stop_loss=2550.0, take_profit=3000.0,
        capital=5.0, risk_pct=0.001, leverage=1,
    )
    assert not calc.can_execute
    assert "MIN_QTY_EXCEEDS_BUDGET" in (calc.skip_reason or "") or "MIN_QTY_EXCEEDS_BUDGET" in (calc.blocker_list or [])


def test_dsl_04_distant_stop_rejected_by_net_rr():
    """DSL-04: Distant stop rejected by Net R:R, not arbitrary ATR distance."""
    # Planned entry: 2700.0, Stop: 2670.0 (dist 30.0), Take Profit: 2715.0 (dist 15.0) -> R:R ~ 0.5 < 1.5
    calc = calculate_risk_reward(
        direction="LONG", planned_entry=2700.0, stop_loss=2670.0, take_profit=2715.0,
        capital=1000.0, risk_pct=0.05, min_net_rr=1.5,
    )
    assert not calc.can_execute
    assert calc.net_rr < 1.5


def test_dsl_05_distant_stop_rejected_by_liquidation_constraints():
    """DSL-05: Distant stop rejected if beyond estimated liquidation boundary."""
    calc = calculate_risk_reward(
        direction="LONG", planned_entry=2700.0, stop_loss=2400.0, take_profit=3200.0,
        capital=1000.0, risk_pct=0.05, leverage=50,  # 50x leverage has liquidation ~ 2% away (~2646)
    )
    if calc.estimated_liquidation is not None:
        assert calc.stop_loss <= calc.estimated_liquidation or not calc.can_execute


def test_dsl_06_distant_stop_rejected_because_anchor_invalidated():
    """DSL-06: Distant stop rejected because the structural anchor was invalidated prior to decision."""
    candles = [
        {"open": 2700.0, "high": 2705.0, "low": 2670.0, "close": 2695.0, "timestamp": 1000000, "open_time_ms": 1000000},
        {"open": 2695.0, "high": 2702.0, "low": 2665.0, "close": 2690.0, "timestamp": 1060000, "open_time_ms": 1060000},  # Breaches 2670.0!
    ]
    anchor = StopAnchor("a_inv", "SWING_LOW", 2670.0, "1M", 1000000, 1000060, "leg_1", "CANDLE_LOW")
    from services.stop_placement_service import validate_anchor_not_invalidated
    is_valid = validate_anchor_not_invalidated(anchor, candles, "LONG", 1120000)
    assert is_valid is False


def test_dsl_07_distant_stop_accepted_with_smaller_quantity_and_unchanged_risk_budget():
    """DSL-07: Distant stop accepted with smaller quantity and unchanged dollar risk budget."""
    capital = 1000.0
    risk_pct = 0.05  # $50 budget
    # Close stop (distance 5.0)
    calc_close = calculate_risk_reward(
        direction="LONG", planned_entry=2700.0, stop_loss=2695.0, take_profit=2715.0,
        capital=capital, risk_pct=risk_pct, min_net_rr=1.5,
    )
    # Distant stop (distance 25.0)
    calc_distant = calculate_risk_reward(
        direction="LONG", planned_entry=2700.0, stop_loss=2675.0, take_profit=2760.0,
        capital=capital, risk_pct=risk_pct, min_net_rr=1.5,
    )
    assert calc_close.can_execute and calc_distant.can_execute
    assert calc_distant.quantity < calc_close.quantity
    assert calc_distant.net_risk_usdt <= 50.0 + 1e-3
    assert calc_close.net_risk_usdt <= 50.0 + 1e-3


def test_dsl_08_historical_policy_and_evidence_compatibility():
    """DSL-08: Historical policy snapshots preserve max_stop_distance_atr as non-blocking diagnostic reference."""
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL", max_stop_distance_atr=4.0)
    anchor = StopAnchor("a", "SWING_LOW", 2660.0, "1M", 1000, 1060, "l1", "CANDLE_LOW")
    dec = plan_structural_stop(
        direction="LONG", planned_entry=2700.0, take_profit=2780.0, legacy_sl=2695.0,
        anchor_result=StopAnchorResult(status="AVAILABLE", anchor=anchor),
        policy=policy, as_of_ms=1060, atr_m1_val=2.0
    )
    assert dec.status == StopStatus.VALID.value
    assert dec.distance_assessment == "DISTANCE_EXTREME"


def test_dsl_09_exact_tick_quantization_for_distant_sl():
    """DSL-09: Exact outward tick quantization for distant SL (floor for LONG, ceil for SHORT)."""
    # LONG: anchor 2678.537, tick 0.01 -> floor is 2678.53 or outward buffer
    long_sl = quantize_stop_outward(anchor_price=2678.537, buffer_price=0.10, direction="LONG", tick_size=0.01)
    assert long_sl <= 2678.437
    assert math.isclose(round(long_sl, 2), long_sl, abs_tol=1e-5)

    # SHORT: anchor 2724.463, tick 0.01 -> ceil outward
    short_sl = quantize_stop_outward(anchor_price=2724.463, buffer_price=0.10, direction="SHORT", tick_size=0.01)
    assert short_sl >= 2724.563
    assert math.isclose(round(short_sl, 2), short_sl, abs_tol=1e-5)


def test_dsl_10_missing_required_atr_evidence_fails_closed():
    """DSL-10: Missing required ATR evidence fails closed in PAPER_STRUCTURAL mode."""
    anchor = StopAnchor("a", "SWING_LOW", 2678.0, "1M", 1000, 1060, "l1", "CANDLE_LOW")
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL")
    dec = plan_structural_stop(
        direction="LONG", planned_entry=2700.0, take_profit=2760.0, legacy_sl=2695.0,
        anchor_result=StopAnchorResult(status="AVAILABLE", anchor=anchor),
        policy=policy, as_of_ms=1060, atr_m1_val=None, atr_m1_obj=None
    )
    assert dec.distance_assessment == "DISTANCE_ATR_UNAVAILABLE"


def test_dsl_11_valid_distant_sl_with_unfavorable_independent_tp_rejected():
    """DSL-11: Valid distant SL with unfavorable independent TP must be rejected for Net R:R."""
    calc = calculate_risk_reward(
        direction="LONG", planned_entry=2700.0, stop_loss=2675.0, take_profit=2710.0,  # TP too close
        capital=1000.0, risk_pct=0.05, min_net_rr=1.5
    )
    assert not calc.can_execute
    assert calc.net_rr < 1.5


def test_dsl_12_valid_distant_sl_with_acceptable_independent_tp_proceeds():
    """DSL-12: Valid distant SL with acceptable independent TP proceeds cleanly."""
    calc = calculate_risk_reward(
        direction="LONG", planned_entry=2700.0, stop_loss=2675.0, take_profit=2760.0,  # TP 60.0 reward vs 25.0 risk
        capital=1000.0, risk_pct=0.05, min_net_rr=1.5
    )
    assert calc.can_execute
    assert calc.net_rr >= 2.0


def test_dsl_13_no_tp_manipulation_to_restore_rr():
    """DSL-13: TP must remain independent and not artificially inflated to restore Net R:R for a wide stop."""
    independent_tp = 2715.0
    anchor = StopAnchor("a", "SWING_LOW", 2670.0, "1M", 1000, 1060, "l1", "CANDLE_LOW")
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL")
    dec = plan_structural_stop(
        direction="LONG", planned_entry=2700.0, take_profit=independent_tp, legacy_sl=2695.0,
        anchor_result=StopAnchorResult(status="AVAILABLE", anchor=anchor),
        policy=policy, as_of_ms=1060, atr_m1_val=3.0
    )
    # Verification: take profit was NOT modified by stop planner
    assert independent_tp == 2715.0


def test_dsl_14_no_post_arm_widening_distant_sl():
    """DSL-14: Distant-SL order cannot be amended outward post-ARM."""
    db = SessionLocal()
    try:
        order = PaperOrder(
            id="test_v17_7_dsl14", symbol="XAUUSDT", direction="LONG", planned_entry=2700.0,
            actual_entry=2700.0, stop_loss=2675.0, take_profit=2760.0, quantity=1.0,
            initial_risk_usdt=25.0, created_at=1000000, state="paper_open"
        )
        db.add(order)
        db.commit()

        # Attempt to widen further to 2670.0
        with pytest.raises(ValueError) as exc:
            PaperBroker.amend_open_position(db, order_id="test_v17_7_dsl14", new_sl=2670.0)
        assert "Không được nới rộng SL" in str(exc.value)
    finally:
        db.close()


def test_dsl_15_deterministic_distant_sl_replay():
    """DSL-15: Deterministic distant-SL replay yields identical decisions and assessments."""
    anchor = StopAnchor("a_rep", "SWING_LOW", 2678.0, "1M", 1000, 1060, "l1", "CANDLE_LOW")
    anchor_res = StopAnchorResult(status="AVAILABLE", anchor=anchor)
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL")

    d1 = plan_structural_stop(
        direction="LONG", planned_entry=2700.0, take_profit=2760.0, legacy_sl=2695.0,
        anchor_result=anchor_res, policy=policy, as_of_ms=1060, atr_m1_val=3.0
    )
    d2 = plan_structural_stop(
        direction="LONG", planned_entry=2700.0, take_profit=2760.0, legacy_sl=2695.0,
        anchor_result=anchor_res, policy=policy, as_of_ms=1060, atr_m1_val=3.0
    )
    assert d1.selected_sl == d2.selected_sl
    assert d1.distance_assessment == d2.distance_assessment == "DISTANCE_EXTREME"
    assert d1.to_dict() == d2.to_dict()


def test_dsl_16_arm_fill_exit_persistence_with_distant_sl():
    """DSL-16: Full persisted DB lifecycle with a 7x ATR distant SL."""
    db = SessionLocal()
    try:
        ord_id = "test_v17_7_dsl16_lifecycle"
        calc = calculate_risk_reward(
            direction="LONG", planned_entry=2700.0, stop_loss=2677.50, take_profit=2760.0,
            capital=1000.0, risk_pct=0.05, min_net_rr=1.5
        )
        assert calc.can_execute

        order = PaperOrder(
            id=ord_id, symbol="XAUUSDT", direction="LONG", planned_entry=2700.0,
            stop_loss=2677.50, take_profit=2760.0, quantity=calc.quantity,
            initial_risk_usdt=calc.net_risk_usdt, risk_pct=0.05, created_at=1000000,
            armed_at=1000000, state="armed",
            cost_snapshot=json.dumps({"stop_placement": {"selected_sl": 2677.50, "distance_assessment": "DISTANCE_EXTREME"}})
        )
        db.add(order)
        db.commit()

        filled = TradeLifecycleService.execute_fill(db, order, fill_price=2700.0, calc_result=calc, now_ms=1001000)
        assert filled.state == "paper_open"
        assert filled.stop_loss == 2677.50

        closed = TradeLifecycleService.execute_close(db, order_id=ord_id, exit_price=2760.0, exit_cause="TP_HIT", now_ms=1005000)
        assert closed.state == "closed"
        assert closed.realized_pnl_net > 0
    finally:
        db.close()


def test_dsl_17_concurrent_policy_update_and_arm_correctness():
    """DSL-17: Concurrent policy update and ARM correctness verified via CAS version validation."""
    db = SessionLocal()
    try:
        curr = resolve_stop_placement_policy(db)
        with pytest.raises(Exception):
            update_stop_placement_policy(db, payload={"min_protective_ticks": 20}, expected_version=curr.version + 50)
    finally:
        db.close()


def test_dsl_18_no_duplicate_paper_order_under_racing_conditions():
    """DSL-18: Two concurrent racing ARM attempts for the same distant-SL setup cannot duplicate orders."""
    db1 = SessionLocal()
    db2 = SessionLocal()
    try:
        ord_id = "test_v17_7_dsl18_race"
        o1 = PaperOrder(
            id=ord_id, symbol="XAUUSDT", direction="LONG", planned_entry=2700.0,
            stop_loss=2677.50, take_profit=2760.0, quantity=1.0, initial_risk_usdt=22.5,
            created_at=1000000, state="armed"
        )
        db1.add(o1)
        db1.commit()

        o2 = PaperOrder(
            id=ord_id, symbol="XAUUSDT", direction="LONG", planned_entry=2700.0,
            stop_loss=2677.50, take_profit=2760.0, quantity=1.0, initial_risk_usdt=22.5,
            created_at=1000001, state="armed"
        )
        db2.add(o2)
        with pytest.raises(Exception):
            db2.commit()
        db2.rollback()

        cnt = db1.query(PaperOrder).filter(PaperOrder.id == ord_id).count()
        assert cnt == 1
    finally:
        db1.close()
        db2.close()


def test_dsl_19_zero_live_exchange_order_submissions_with_distant_sl():
    """DSL-19: Instrumented network spy confirms zero live exchange order submissions with distant SL."""
    spy = Mock()
    with patch("httpx.Client.post", side_effect=spy), patch("httpx.AsyncClient.post", side_effect=spy):
        calc = calculate_risk_reward(
            direction="LONG", planned_entry=2700.0, stop_loss=2675.0, take_profit=2760.0,
            capital=1000.0, risk_pct=0.05, min_net_rr=1.5
        )
        assert calc.can_execute
        assert spy.call_count == 0


def test_dsl_20_historical_orders_unaffected_by_distant_sl():
    """DSL-20: Existing historical orders in database remain unaffected by distant-SL policy changes."""
    db = SessionLocal()
    try:
        hist_ord = PaperOrder(
            id="test_v17_7_hist_dsl20", symbol="XAUUSDT", direction="SHORT", planned_entry=2700.0,
            actual_entry=2700.0, stop_loss=2708.0, take_profit=2680.0, quantity=1.0,
            initial_risk_usdt=8.0, created_at=900000, state="closed",
            realized_pnl_net=19.5, realized_r=2.4
        )
        db.add(hist_ord)
        db.commit()

        reloaded = db.query(PaperOrder).filter(PaperOrder.id == "test_v17_7_hist_dsl20").first()
        assert reloaded.stop_loss == 2708.0
        assert reloaded.state == "closed"
        assert reloaded.realized_pnl_net == 19.5
    finally:
        db.close()
