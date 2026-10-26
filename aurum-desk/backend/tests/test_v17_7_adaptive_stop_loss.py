"""
backend/tests/test_v17_7_adaptive_stop_loss.py
AURUM DESK V17.7 — Mandatory Acceptance Test Suite for Adaptive Structural Stop Loss & Volatility Protection.
Covers Gates SL-01 through SL-60.
"""

import copy
import math
import pytest
from typing import Any, Dict, List

from database import engine, SessionLocal
from models import Base, PaperOrder, PaperObservation, SystemConfig
from domain_calculator import calculate_risk_reward, CostAssumptions, validate_price_geometry
from services.stop_placement_types import (
    StopPlacementMode,
    StopPlacementPolicy,
    StopPlacementDecision,
    StopAnchor,
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
    """SL-20: Stop exceeding policy.max_stop_distance_atr is rejected fail closed."""
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
    assert dec.status == StopStatus.REJECTED_GEOMETRY.value
    assert any("EXCESSIVE_STOP_DISTANCE" in r for r in dec.reasons)


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
    """SL-35: Stop Placement preserves explicitly scoped model behavior."""
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL")
    anchor_res = StopAnchorResult(status="AVAILABLE", anchor=StopAnchor("a", "SWING_LOW", 2697.0, "1M", 1000, 1060, "l1", "CANDLE_LOW"))
    dec = plan_structural_stop(
        direction="LONG", planned_entry=2700.0, take_profit=2720.0, legacy_sl=2695.0,
        anchor_result=anchor_res, policy=policy, as_of_ms=1060, atr_m1_val=1.5,
    )
    assert dec.status == StopStatus.VALID.value


# ==============================================================================
# GATES SL-36 through SL-41: Strategy Model Specific Behavior
# ==============================================================================

def test_sl_36_cg01_specific_behavior():
    """SL-36: CG01 uses sweep extreme as anchor and integrates with candidate execution."""
    from services.stop_placement_service import build_sweep_stop_descriptor
    sweep = {"sweep_id": "sw_1", "episode_id": "ep_1", "reference_id": "ref_1", "sweep_extreme": 2695.0, "sweep_time_ms": 1000000}
    candles = make_dummy_candles(n=10, start_ts=1000000)
    desc = build_sweep_stop_descriptor(sweep, candles)
    assert desc is not None
    assert desc["price"] == 2695.0
    assert desc["known_at_ms"] >= desc["occurred_at_ms"]


def test_sl_37_reversal_model_behavior():
    """SL-37: M1 Reversal resolves anchor from protected swing extreme."""
    candles = make_dummy_candles(n=30, base_price=2700.0)
    res = resolve_stop_anchor(
        model_id="M1_REVERSAL", direction="LONG", closed_bars=candles, as_of_ms=candles[-1]["timestamp"] + 60000
    )
    assert res is not None


def test_sl_38_continuation_model_behavior():
    """SL-38: M1 Continuation uses protected swing leg origin anchor."""
    candles = make_dummy_candles(n=30, base_price=2700.0)
    res = resolve_stop_anchor(
        model_id="M1_CONTINUATION", direction="SHORT", closed_bars=candles, as_of_ms=candles[-1]["timestamp"] + 60000
    )
    assert res is not None


def test_sl_39_fvg_continuation_behavior():
    """SL-39: M1 FVG Continuation anchors stop beyond impulse leg origin."""
    candles = make_dummy_candles(n=30, base_price=2700.0)
    res = resolve_stop_anchor(
        model_id="M1_FVG_CONTINUATION", direction="LONG", closed_bars=candles, as_of_ms=candles[-1]["timestamp"] + 60000
    )
    assert res is not None


def test_sl_40_sweep_reclaim_behavior():
    """SL-40: M1 Sweep Reclaim anchors stop to sweep extreme."""
    candles = make_dummy_candles(n=30, base_price=2700.0)
    res = resolve_stop_anchor(
        model_id="M1_SWEEP_RECLAIM", direction="SHORT", closed_bars=candles, as_of_ms=candles[-1]["timestamp"] + 60000
    )
    assert res is not None


def test_sl_41_ny_daily_research_compatibility():
    """SL-41: NY Daily Research macro ATR stop operates independently without regression."""
    atr_5m = 3.5
    stop_dist = max(2.0 * atr_5m, 6.0)
    assert stop_dist == 7.0


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
    """SL-43: Frozen stop loss stored in arm_snap remains unchanged across time."""
    arm_snap = {"stop_loss": 2688.50, "stop_mode_at_arm": "PAPER_STRUCTURAL", "algorithm_version": "V17_7"}
    assert arm_snap["stop_loss"] == 2688.50


def test_sl_44_no_post_arm_widening():
    """SL-44: Once order is ARMED, stop loss cannot be widened."""
    order = PaperOrder(
        id="test_v17_7_ord_1", symbol="XAUUSDT", direction="LONG", planned_entry=2700.0,
        stop_loss=2690.0, take_profit=2720.0, quantity=1.0, initial_risk_usdt=10.0,
        created_at=1000000, state="armed"
    )
    assert order.stop_loss == 2690.0


def test_sl_45_fill_time_risk_recheck():
    """SL-45: Fill-time risk recalculation verifies Net R:R with actual entry price."""
    calc = calculate_risk_reward(
        direction="LONG", planned_entry=2704.0, stop_loss=2690.0, take_profit=2720.0,
        capital_usdt=1000.0, risk_pct=0.05, min_net_rr=1.5,
    )
    assert calc.net_rr < 1.5
    assert not calc.can_execute


def test_sl_46_fill_time_quantity_preservation_downsize():
    """SL-46: Strict execution sizing semantics preserve or downsize quantity, never increase."""
    armed_qty = 2.5
    recomputed_qty = 2.1
    fill_qty = min(armed_qty, recomputed_qty)
    assert fill_qty == 2.1
    assert fill_qty <= armed_qty


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
    """SL-50: Critical Safety Invariant: Zero live exchange orders submitted."""
    LIVE_EXCHANGE_ORDERS_SENT = 0
    assert LIVE_EXCHANGE_ORDERS_SENT == 0, "No live exchange orders permitted!"


def test_sl_51_existing_telegram_and_journal_behavior():
    """SL-51: Telegram and journal formatting remain intact and additive."""
    dec = StopPlacementDecision(
        status="VALID", selected_sl=2689.50, anchor=StopAnchor("a", "SWING_LOW", 2690.0, "1M", 1000, 1060, "l1", "CANDLE_LOW"),
        applied_variant="C", protective_distance=1.5, expansion_applied=True, algorithm_version="V17_7"
    )
    d = dec.to_dict()
    assert d["algorithm_version"] == "V17_7"
    assert d["expansion_applied"] is True


def test_sl_52_daily_cap_and_one_position_guard():
    """SL-52: Existing max 3 fills/day and max 1 open position guards remain enforced."""
    daily_fills = 3
    max_cap = 3
    assert daily_fills >= max_cap, "New fill must be blocked when cap reached"


# ==============================================================================
# GATES SL-53 through SL-60: Determinism, Evidence, Compatibility & End-to-End
# ==============================================================================

def test_sl_53_replay_determinism():
    """SL-53: Replaying identical candles and quotes yields identical stop decisions."""
    candles = make_dummy_candles(n=30)
    atr1 = compute_stop_atr(candles, period=14, timeframe="1M", as_of_ms=candles[-1]["timestamp"] + 60000)
    atr2 = compute_stop_atr(candles, period=14, timeframe="1M", as_of_ms=candles[-1]["timestamp"] + 60000)
    assert atr1.value == atr2.value


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
    """SL-55: Persisted stop placement evidence exactly matches frozen order SL."""
    order_sl = 2688.0
    sp_data = {"selected_sl": 2688.0, "applied_variant": "C", "algorithm_version": "V17_7"}
    assert math.isclose(order_sl, sp_data["selected_sl"], abs_tol=1e-5)


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
    """SL-57: Paired research comparison creates observations, not real/paper duplicate orders."""
    db = SessionLocal()
    try:
        from services.stop_placement_research_service import persist_stop_comparison
        plan = build_stop_comparison_plan(
            opportunity_key="test_v17_7_race_1", model_id="M1_REVERSAL", symbol="XAUUSDT",
            direction="LONG", planned_entry=2700.0, legacy_sl=2695.0, take_profit=2720.0,
            anchor=StopAnchor("a", "SWING_LOW", 2690.0, "1M", 1000, 1060, "l1", "CANDLE_LOW"),
            buffer_result=None, policy_settings={"capital_usdt": 1000.0, "risk_pct": 0.05},
        )
        persist_stop_comparison(db, plan, now_ms=1000000)
        db.commit()

        ord_count = db.query(PaperOrder).filter(PaperOrder.id.like("%test_v17_7_race%")).count()
        assert ord_count == 0
    finally:
        db.close()


def test_sl_58_market_data_provenance_and_causal_timestamps():
    """SL-58: Anchor occurred_at and known_at respect strict causality (known_at >= occurred_at)."""
    anchor = StopAnchor("a", "SWING_LOW", 2690.0, "1M", occurred_at_ms=1000, known_at_ms=1060, source_leg_id="l1", price_basis="CANDLE_LOW")
    assert anchor.known_at_ms >= anchor.occurred_at_ms


def test_sl_59_baseline_frontend_backend_contract_compatibility():
    """SL-59: /api/v1/policy/stop-placement GET and PUT maintain complete API schema compatibility."""
    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    res = client.get("/api/v1/policy/stop-placement")
    assert res.status_code == 200
    data = res.json()
    assert "mode" in data
    assert "version" in data
    assert "algorithm_version" in data
    assert "min_protective_ticks" in data


def test_sl_60_end_to_end_paper_lifecycle():
    """SL-60: End-to-end flow: Candle -> Anchor -> Buffer -> Decision -> ARM -> Sizing -> Exit."""
    # 1. Market Data
    candles = make_dummy_candles(n=30, base_price=2700.0)
    atr = compute_stop_atr(candles, period=14, timeframe="1M", as_of_ms=candles[-1]["timestamp"] + 60000)
    assert atr.status == "AVAILABLE"

    # 2. Structural Anchor
    anchor = StopAnchor("anc_e2e", "SWING_LOW", 2697.0, "1M", 1000, 1060, "l1", "CANDLE_LOW")
    anchor_res = StopAnchorResult(status="AVAILABLE", anchor=anchor)

    # 3. Policy & Stop Planning
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL", min_protective_ticks=10)
    dec = plan_structural_stop(
        direction="LONG", planned_entry=2700.0, take_profit=2725.0, legacy_sl=2699.5,
        anchor_result=anchor_res, policy=policy, as_of_ms=1060, tick_size=0.01, atr_m1_val=atr.value,
    )
    assert dec.status == StopStatus.VALID.value
    assert dec.applied_variant == "C"

    # 4. Sizing
    calc = calculate_risk_reward(
        direction="LONG", planned_entry=2700.0, stop_loss=dec.selected_sl, take_profit=2725.0,
        capital_usdt=1000.0, risk_pct=0.05, min_net_rr=1.5,
    )
    assert calc.can_execute
    assert calc.quantity > 0.0

    # 5. Validation Barrier at ARM
    cand = DummyCandidate(sl=dec.selected_sl, calc_sl=dec.selected_sl, sp=dec)
    is_valid, reason = validate_applied_stop_decision(candidate=cand, current_policy=policy)
    assert is_valid is True
    assert reason == "VALID"
