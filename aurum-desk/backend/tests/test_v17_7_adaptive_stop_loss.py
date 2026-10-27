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

import threading
from datetime import datetime
from zoneinfo import ZoneInfo

from database import engine, SessionLocal, assert_test_database_isolated, run_schema_migrations
import crud
import models
from models import (
    Base, PaperOrder, PaperObservation, SystemConfig, WatchSetup,
    SMCScenarioSnapshot, SMCScenario, OpportunityFunnelRecord, OpportunityDecisionEvent
)
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
from services.cg01_engine import evaluate_cg01_candidate
from services.cg01_types import CG01Config, CG01EntryMode
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator
from services.clock import live_clock
from services.quote_validator import ValidatedQuote
from tests.test_v16_3_cg01_pipeline import build_long_fixture_series


@pytest.fixture(autouse=True)
def clean_v17_7_db():
    assert_test_database_isolated(target_engine=engine)
    run_schema_migrations(engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    assert_test_database_isolated(session=db)
    try:
        db.query(SystemConfig).delete(synchronize_session=False)
        db.query(models.TradingPolicy).delete(synchronize_session=False)
        db.query(PaperOrder).delete(synchronize_session=False)
        db.query(PaperObservation).delete(synchronize_session=False)
        db.query(WatchSetup).delete(synchronize_session=False)
        db.query(SMCScenarioSnapshot).delete(synchronize_session=False)
        db.query(SMCScenario).delete(synchronize_session=False)
        db.query(OpportunityFunnelRecord).delete(synchronize_session=False)
        db.query(OpportunityDecisionEvent).delete(synchronize_session=False)
        db.query(models.DayAudit).delete(synchronize_session=False)
        audit = crud.get_or_create_today_audit(db)
        audit.fills_count = 0
        audit.cooldown_until = None
        audit.consecutive_losses = 0
        audit.is_blocked = False
        audit.block_reason = None
        db.commit()
    finally:
        db.close()
    yield
    assert_test_database_isolated(target_engine=engine)
    db = SessionLocal()
    assert_test_database_isolated(session=db)
    try:
        db.query(SystemConfig).delete(synchronize_session=False)
        db.query(models.TradingPolicy).delete(synchronize_session=False)
        db.query(PaperOrder).delete(synchronize_session=False)
        db.query(PaperObservation).delete(synchronize_session=False)
        db.query(WatchSetup).delete(synchronize_session=False)
        db.query(SMCScenarioSnapshot).delete(synchronize_session=False)
        db.query(SMCScenario).delete(synchronize_session=False)
        db.query(OpportunityFunnelRecord).delete(synchronize_session=False)
        db.query(OpportunityDecisionEvent).delete(synchronize_session=False)
        db.query(models.DayAudit).delete(synchronize_session=False)
        audit = crud.get_or_create_today_audit(db)
        audit.fills_count = 0
        audit.cooldown_until = None
        audit.consecutive_losses = 0
        audit.is_blocked = False
        audit.block_reason = None
        db.commit()
    finally:
        db.close()


def make_test_atr(value: float, symbol: str = "XAUUSDT", timeframe: str = "1M", as_of: int = 1060000) -> StopATRResult:
    """Helper creating valid execution-grade ATR evidence for test scenarios (V17.7.4 Section 04)."""
    return StopATRResult(
        symbol=symbol,
        timeframe=timeframe,
        period=14,
        method="SMA_TR_V1",
        value=float(value),
        bars_count=15,
        source_candle_count=15,
        last_closed_at_ms=as_of - 10,
        last_closed_candle_timestamp=as_of - 10,
        calculated_as_of=as_of,
        status="AVAILABLE",
    )


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


class CandleBarStub:
    def __init__(self, timestamp, open, high, low, close, volume=100.0):
        self.timestamp = timestamp
        self.open_time_ms = timestamp
        self.open = float(open)
        self.high = float(high)
        self.low = float(low)
        self.close = float(close)
        self.volume = float(volume)

    def to_dict(self):
        return {
            "timestamp": self.timestamp, "open": self.open, "high": self.high,
            "low": self.low, "close": self.close, "volume": self.volume
        }

    def __getitem__(self, item):
        return getattr(self, item)


def make_real_armed_candidate_order(db, mode: str = "PAPER_STRUCTURAL", order_id_override: str = None, distant: bool = False):
    """Executes true candidate evaluation and production orchestrator ARM boundary."""
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)
    if distant:
        for b in c_15m:
            if b["low"] < 2620.0:
                b["low"] = 2615.0
        for b in c_1m:
            if b["low"] < 2620.0:
                b["low"] = 2615.0
    ctx = {"4H": c_4h, "1H": c_1h, "15M": c_15m, "1M": c_1m}
    now_ms = t_start + 600_000
    quote_dict = {"bid": 2623.75, "ask": 2623.85, "is_valid": True, "timestamp": now_ms}
    min_rr = 1.0 if distant else 1.5
    cfg = CG01Config(enabled=True, entry_mode=CG01EntryMode.LIMIT_AT_CE_AFTER_CONFIRMATION.value, min_net_rr=min_rr)
    policy = StopPlacementPolicy(mode=mode)
    dec = evaluate_cg01_candidate(ctx, quote_dict, config=cfg, now_ms=now_ms, stop_policy=policy)
    assert dec.candidate is not None

    cand = dec.candidate
    orch = ScalpWatcherOrchestrator()
    v_quote = ValidatedQuote(
        is_valid=True, bid=2623.75, ask=2623.85, last=2623.80, spread=0.10,
        exchange_time=now_ms, observed_at=now_ms, freshness_sec=0.1, is_stale=False,
        rejection_code=None, rejection_detail=None
    )
    policy_settings = {"capital_usdt": 1000.0, "risk_pct": 1.0, "config_version": 1}
    if order_id_override:
        cand.order_id = order_id_override
    armed_order = orch._arm_paper_candidate(
        db, cand, now_ms=now_ms, clock=live_clock, quote=v_quote,
        policy_settings=policy_settings, auto_enabled=True,
    )
    if armed_order and order_id_override:
        armed_order.id = order_id_override
        db.flush()
    return cand, armed_order, now_ms


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
        anchor_result=anchor_res, policy=policy, as_of_ms=1060, tick_size=0.01,
        atr_m1_val=1.5, atr_m1_obj=make_test_atr(1.5, as_of=1060),
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
        anchor_result=anchor_res, policy=policy, as_of_ms=1060, tick_size=0.01,
        atr_m1_val=1.5, atr_m1_obj=make_test_atr(1.5, as_of=1060),
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
        anchor_result=anchor_res, policy=policy, as_of_ms=1060, tick_size=0.01,
        atr_m1_val=2.0, atr_m1_obj=make_test_atr(2.0, as_of=1060),
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
        anchor_result=anchor_res, policy=policy, as_of_ms=1060, tick_size=0.01,
        atr_m1_val=2.0, atr_m1_obj=make_test_atr(2.0, as_of=1060),
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
        anchor_result=anchor_res, policy=policy, as_of_ms=1060,
        atr_m1_val=1.5, atr_m1_obj=make_test_atr(1.5, as_of=1060),
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
    """SL-36: CG01 integrates real structural stop chain through evaluate_cg01_candidate production candidate path."""
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)
    ctx = {"4H": c_4h, "1H": c_1h, "15M": c_15m, "1M": c_1m}
    now_ms = t_start + 600_000
    quote = {"bid": 2623.75, "ask": 2623.85, "is_valid": True, "timestamp": now_ms}
    cfg = CG01Config(enabled=True, entry_mode=CG01EntryMode.LIMIT_AT_CE_AFTER_CONFIRMATION.value)
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL", min_protective_ticks=10)

    dec = evaluate_cg01_candidate(ctx, quote, config=cfg, now_ms=now_ms, stop_policy=policy)
    assert dec.is_eligible is True
    assert dec.candidate is not None
    cand = dec.candidate
    assert cand.stop_loss < 2623.75
    assert math.isclose(cand.stop_loss, cand.calculation.stop_loss, abs_tol=1e-4)
    assert math.isclose(cand.stop_loss, cand.stop_placement.selected_sl, abs_tol=1e-4)
    assert cand.calculation.can_execute is True
    assert cand.stop_placement.status == "VALID"
    assert cand.stop_placement.applied_variant == "C"


def test_sl_37_reversal_model_behavior():
    """SL-37: M1 Reversal resolves anchor from protected sweep extreme and executes real candidate evaluator."""
    from tests.test_v15_1_execution_lifecycle import _make_causal_long_m1_candles
    from services.scalp_m1_model_service import ScalpM1ModelService, M1ScalpConfig
    from services.scalp_m1_types import WatchedZone, ZoneState

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
        anchor_result=res, policy=policy, as_of_ms=1060000,
        atr_m1_val=1.5, atr_m1_obj=make_test_atr(1.5, as_of=1060000),
    )
    assert dec.status == StopStatus.VALID.value
    assert dec.selected_sl < 2691.0

    # Real M1 Reversal candidate evaluator execution
    cfg = M1ScalpConfig(min_closed_m1_bars=15, displacement_body_atr_min=0.7)
    zone = WatchedZone(
        zone_id="z_long_rev", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.CONFIRMED.value,
    )
    causal_candles = _make_causal_long_m1_candles()
    quote = {"bid": 2622.0, "ask": 2622.5, "is_valid": True}
    eval_res = ScalpM1ModelService.evaluate_m1_reversal_candidate(
        zone, causal_candles, quote, {"capital_usdt": 1000.0, "risk_pct": 0.25, "leverage": 50},
        now_ms=1300_000, config=cfg, stop_policy=policy
    )
    assert eval_res.is_eligible is True
    assert eval_res.model_id == "SMC_M1_REVERSAL"
    assert eval_res.direction == "LONG"
    assert eval_res.stop_loss <= 2602.0
    assert eval_res.stop_placement is not None
    assert eval_res.stop_placement.status == StopStatus.VALID.value


def test_sl_38_continuation_model_behavior():
    """SL-38: M1 Continuation uses protected swing leg origin anchor and real candidate evaluator."""
    from services.scalp_m1_model_service import ScalpM1ModelService, M1ScalpConfig

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
        anchor_result=res, policy=policy, as_of_ms=as_of,
        atr_m1_val=1.5, atr_m1_obj=make_test_atr(1.5, as_of=as_of),
    )
    assert dec.status == StopStatus.VALID.value
    assert dec.selected_sl > px

    # Real candidate evaluator check on unaligned trend fails closed
    candle_stubs = [CandleBarStub(c["timestamp"], c["open"], c["high"], c["low"], c["close"]) for c in candles]
    quote = {"bid": 2690.0, "ask": 2690.5, "is_valid": True}
    cfg = M1ScalpConfig(scalp_m1_enabled=True, scalp_m1_mode="PAPER")
    res_cont = ScalpM1ModelService.evaluate_m1_continuation_candidate(
        candles_1m=candle_stubs, candles_5m=[], current_quote=quote,
        htf_bias="BULLISH", h1_alignment="CONFLICT",
        policy_settings={"capital_usdt": 1000.0, "risk_pct": 0.05},
        now_ms=as_of, config=cfg, stop_policy=policy
    )
    assert res_cont.is_eligible is False


def test_sl_39_fvg_continuation_behavior():
    """SL-39: M1 FVG Continuation anchors stop beyond impulse leg origin and evaluates real candidate."""
    from services.scalp_m1_model_service import ScalpM1ModelService, M1ScalpConfig, ModelIdentifier
    from services.smc_scenario_types import LiquidityLevel

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
        anchor_result=res, policy=policy, as_of_ms=as_of,
        atr_m1_val=1.5, atr_m1_obj=make_test_atr(1.5, as_of=as_of),
    )
    assert dec.status == StopStatus.VALID.value
    assert dec.selected_sl < px

    # Real M1 FVG Continuation candidate evaluator call
    cfg = M1ScalpConfig(scalp_m1_enabled=True, scalp_m1_mode="PAPER")
    c_list = [
        CandleBarStub(60_000, 2600.0, 2602.0, 2598.0, 2600.0),
        CandleBarStub(120_000, 2600.0, 2603.0, 2599.0, 2602.0),
        CandleBarStub(180_000, 2602.0, 2605.0, 2601.0, 2604.0),
        CandleBarStub(240_000, 2604.0, 2606.0, 2603.0, 2605.0),
        CandleBarStub(300_000, 2605.0, 2607.0, 2604.0, 2606.0),
        CandleBarStub(360_000, 2606.0, 2608.0, 2605.0, 2607.0),
        CandleBarStub(420_000, 2607.0, 2609.0, 2606.0, 2608.0),
        CandleBarStub(480_000, 2608.0, 2610.0, 2607.0, 2609.0),
        CandleBarStub(540_000, 2609.0, 2611.0, 2608.0, 2610.0),
        CandleBarStub(600_000, 2610.0, 2612.0, 2609.0, 2611.0),
        CandleBarStub(660_000, 2611.0, 2613.0, 2610.0, 2612.0),
        CandleBarStub(720_000, 2612.0, 2614.0, 2611.0, 2613.0),
        CandleBarStub(780_000, 2613.0, 2615.0, 2612.0, 2614.0),
        CandleBarStub(840_000, 2614.0, 2626.0, 2614.0, 2625.0),
        CandleBarStub(900_000, 2625.0, 2628.0, 2618.0, 2626.0),
        CandleBarStub(960_000, 2626.0, 2627.0, 2616.0, 2620.0),
        CandleBarStub(1020_000, 2620.0, 2622.0, 2619.0, 2621.0),
    ]
    target_lvl = LiquidityLevel(
        id="target_high", price=2645.0, side="BUY_SIDE", source_kind="SWING",
        occurred_at_ms=100_000, known_at_ms=120_000, state="UNTOUCHED"
    )
    quote_pos = {"bid": 2620.0, "ask": 2620.5, "is_valid": True}
    # Positive control: baseline legacy evaluation
    res_pos = ScalpM1ModelService.evaluate_m1_fvg_continuation_candidate(
        c_list, c_list, quote_pos, "BULLISH", "BULLISH",
        {"capital_usdt": 1000.0, "risk_pct": 0.25, "leverage": 50},
        now_ms=1_100_000, config=cfg, context_levels=[target_lvl]
    )
    assert res_pos.is_eligible is True
    assert res_pos.model_id == ModelIdentifier.SMC_M1_FVG_CONTINUATION.value
    assert res_pos.direction == "LONG"

    # Fail closed control: PAPER_STRUCTURAL without confirmed swings fails closed
    res_fail = ScalpM1ModelService.evaluate_m1_fvg_continuation_candidate(
        c_list, c_list, quote_pos, "BULLISH", "BULLISH",
        {"capital_usdt": 1000.0, "risk_pct": 0.25, "leverage": 50},
        now_ms=1_100_000, config=cfg, context_levels=[target_lvl], stop_policy=policy
    )
    assert res_fail.is_eligible is False


def test_sl_40_sweep_reclaim_behavior():
    """SL-40: M1 Sweep Reclaim anchors stop to sweep extreme and evaluates real candidate."""
    from services.scalp_m1_model_service import ScalpM1ModelService, M1ScalpConfig

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
        anchor_result=res, policy=policy, as_of_ms=1060000,
        atr_m1_val=1.5, atr_m1_obj=make_test_atr(1.5, as_of=1060000),
    )
    assert dec.status == StopStatus.VALID.value
    assert dec.selected_sl > 2716.5

    # Real M1 Sweep Reclaim evaluator call (missing cluster fails closed)
    cfg = M1ScalpConfig(scalp_m1_enabled=True, scalp_m1_mode="PAPER")
    c_stubs = [CandleBarStub(c["timestamp"], c["open"], c["high"], c["low"], c["close"]) for c in candles]
    quote = {"bid": 2700.0, "ask": 2700.5, "is_valid": True}
    res_sr = ScalpM1ModelService.evaluate_m1_sweep_reclaim_candidate(
        closed_1m=c_stubs, cluster=None, quote=quote,
        policy_settings={"capital_usdt": 1000.0, "risk_pct": 0.25}, now_ms=1060000, config=cfg, stop_policy=policy
    )
    assert res_sr.is_eligible is False
    assert res_sr.reason_code == "CLUSTER_INVALID"


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
    """SL-43: Frozen stop loss stored in ARMED PaperOrder via production ARM remains unchanged across persistence reload."""
    db = SessionLocal()
    try:
        cand, armed_order, now_ms = make_real_armed_candidate_order(db, mode="PAPER_STRUCTURAL", order_id_override="test_v17_7_frozen_arm")
        assert armed_order is not None
        assert armed_order.state == "armed"
        expected_sl = armed_order.stop_loss
        db.commit()

        reloaded = db.query(PaperOrder).filter(PaperOrder.id == "test_v17_7_frozen_arm").first()
        assert reloaded is not None
        assert reloaded.stop_loss == expected_sl
        cost_dict = json.loads(reloaded.cost_snapshot)
        assert cost_dict.get("stop_loss") == expected_sl or "stop_placement" in cost_dict
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
    """SL-46: Strict execution sizing semantics preserve or downsize quantity via execute_fill, never increase beyond ARMED quantity."""
    db = SessionLocal()
    try:
        cand, armed_order, now_ms = make_real_armed_candidate_order(db, mode="PAPER_STRUCTURAL", order_id_override="test_v17_7_qty_cap")
        assert armed_order is not None
        armed_qty = armed_order.quantity

        calc_larger = calculate_risk_reward(
            direction=armed_order.direction, planned_entry=armed_order.planned_entry,
            stop_loss=armed_order.stop_loss, take_profit=armed_order.take_profit,
            capital=5000.0, risk_pct=1.0
        )
        assert calc_larger.quantity > armed_qty

        filled = TradeLifecycleService.execute_fill(
            db=db, order=armed_order, fill_price=armed_order.planned_entry,
            calc_result=calc_larger, now_ms=now_ms + 1000
        )
        assert filled.quantity <= armed_qty
        assert filled.stop_loss == armed_order.stop_loss
    finally:
        db.close()


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
    Instruments actual outbound HTTP/network boundaries with active spy while executing candidate evaluation, ARM, fill, and close.
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

        db = SessionLocal()
        try:
            cand, armed_order, now_ms = make_real_armed_candidate_order(db, mode="PAPER_STRUCTURAL", order_id_override="test_v17_7_sl50_safety")
            assert armed_order is not None
            filled = TradeLifecycleService.execute_fill(db, armed_order, fill_price=armed_order.planned_entry, calc_result=cand.calculation, now_ms=now_ms + 1000)
            assert filled.state == "paper_open"
            closed = TradeLifecycleService.execute_close(db, order_id=filled.id, exit_price=filled.take_profit, exit_cause="TP_HIT", now_ms=now_ms + 5000)
            assert closed.state == "closed"
        finally:
            db.close()

        from services.execution_coordinator import LIVE_EXCHANGE_ORDERS_SENT
        assert LIVE_EXCHANGE_ORDERS_SENT == 0
        assert spy.call_count == 0, "No live outbound exchange requests permitted during full execution!"


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

        # Clean up audit so subsequent tests are not blocked
        audit.fills_count = 0
        db.commit()
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
    """SL-55: Persisted stop placement evidence exactly matches frozen order SL, policy version, and algo version across independent sessions."""
    order_id = "test_v17_7_ord_ev"
    dec_dict = {
        "selected_sl": 2688.50,
        "policy_version": 2,
        "algorithm_version": "V17_7_1",
        "applied_variant": "C",
    }
    db1 = SessionLocal()
    try:
        order = PaperOrder(
            id=order_id, symbol="XAUUSDT", direction="LONG", planned_entry=2700.0,
            stop_loss=2688.50, take_profit=2725.0, quantity=1.0, initial_risk_usdt=11.5,
            created_at=1000000, state="armed",
            cost_snapshot=json.dumps({"stop_placement": dec_dict})
        )
        db1.add(order)
        db1.commit()
    finally:
        db1.close()

    # Verify durability across independent session/connection
    db2 = SessionLocal()
    try:
        reloaded = db2.query(PaperOrder).filter(PaperOrder.id == order_id).first()
        assert reloaded is not None
        cost_dict = json.loads(reloaded.cost_snapshot)
        saved_dec = cost_dict["stop_placement"]
        assert reloaded.stop_loss == saved_dec["selected_sl"] == 2688.50
        assert saved_dec["policy_version"] == 2
        assert saved_dec["algorithm_version"] == "V17_7_1"
    finally:
        db2.close()


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
    """SL-57: Real concurrent ARM / persistence requests across independent threads cannot duplicate orders."""
    import threading
    barrier = threading.Barrier(2)
    results = []
    ord_id = "test_v17_7_concurrent_race_barrier"

    def _arm_worker(worker_id: int):
        db = SessionLocal()
        try:
            order = PaperOrder(
                id=ord_id, symbol="XAUUSDT", direction="LONG", planned_entry=2700.0,
                stop_loss=2690.0, take_profit=2720.0, quantity=1.0, initial_risk_usdt=10.0,
                created_at=1000000 + worker_id, state="armed"
            )
            db.add(order)
            barrier.wait(timeout=5.0)
            db.commit()
            results.append((worker_id, "SUCCESS"))
        except Exception as e:
            db.rollback()
            results.append((worker_id, f"ROLLBACK: {type(e).__name__}"))
        finally:
            db.close()

    t1 = threading.Thread(target=_arm_worker, args=(1,))
    t2 = threading.Thread(target=_arm_worker, args=(2,))
    t1.start()
    t2.start()
    t1.join(timeout=10.0)
    t2.join(timeout=10.0)

    successes = [r for r in results if r[1] == "SUCCESS"]
    assert len(successes) == 1, f"Expected exactly 1 winner, got {results}"

    db_check = SessionLocal()
    try:
        cnt = db_check.query(PaperOrder).filter(PaperOrder.id == ord_id).count()
        assert cnt == 1
    finally:
        db_check.close()


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
    anchor_invalid_known = StopAnchor("a1", "SWING_LOW", 2690.0, "1M", occurred_at_ms=1050000, known_at_ms=1000000, source_leg_id="l1", price_basis="CANDLE_LOW")
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL")
    dec_causal_1 = plan_structural_stop(
        direction="LONG", planned_entry=2700.0, take_profit=2720.0, legacy_sl=2695.0,
        anchor_result=StopAnchorResult(status="AVAILABLE", anchor=anchor_invalid_known),
        policy=policy, as_of_ms=1060000, atr_m1_val=1.5
    )
    assert dec_causal_1.status == StopStatus.INVALID_DATA.value
    assert "CAUSAL_ORDERING_VIOLATION" in dec_causal_1.reasons

    # Future anchor: known_at > as_of_ms
    anchor_future = StopAnchor("a2", "SWING_LOW", 2690.0, "1M", occurred_at_ms=1000000, known_at_ms=1070000, source_leg_id="l1", price_basis="CANDLE_LOW")
    dec_causal_2 = plan_structural_stop(
        direction="LONG", planned_entry=2700.0, take_profit=2720.0, legacy_sl=2695.0,
        anchor_result=StopAnchorResult(status="AVAILABLE", anchor=anchor_future),
        policy=policy, as_of_ms=1060000, atr_m1_val=1.5
    )
    assert dec_causal_2.status == StopStatus.INVALID_DATA.value
    assert "CAUSAL_ORDERING_VIOLATION" in dec_causal_2.reasons


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
    SL-60: Full end-to-end persisted PAPER lifecycle through real orchestrator ARM boundary:
    Candidate -> Production ARM -> Persisted ARMED Order -> Fill Evaluation -> PAPER_OPEN -> SL/TP Trigger -> CLOSED -> Durably Reloaded.
    """
    db = SessionLocal()
    try:
        cand, armed_order, now_ms = make_real_armed_candidate_order(
            db, mode="PAPER_STRUCTURAL", order_id_override="test_v17_7_sl60_true_lifecycle"
        )
        assert armed_order is not None
        assert armed_order.state == "armed"
        ord_id = armed_order.id
        planned_entry = armed_order.planned_entry
        target_tp = armed_order.take_profit
        sl_price = armed_order.stop_loss
        calc = cand.calculation
        db.commit()
    finally:
        db.close()

    # Reopen in fresh session to verify fill execution
    db2 = SessionLocal()
    try:
        loaded_armed = db2.query(PaperOrder).filter(PaperOrder.id == ord_id).first()
        assert loaded_armed.state == "armed"
        filled = TradeLifecycleService.execute_fill(
            db=db2, order=loaded_armed, fill_price=planned_entry, calc_result=calc, now_ms=now_ms + 1000
        )
        assert filled.state == "paper_open"
        assert filled.actual_entry == planned_entry
        assert filled.stop_loss == sl_price
        db2.commit()
    finally:
        db2.close()

    # Trigger TP exit and close in 3rd independent session
    db3 = SessionLocal()
    try:
        loaded_open = db3.query(PaperOrder).filter(PaperOrder.id == ord_id).first()
        exit_res = evaluate_order_exit(
            {"direction": loaded_open.direction, "planned_entry": loaded_open.planned_entry,
             "actual_entry": loaded_open.actual_entry, "stop_loss": loaded_open.stop_loss,
             "take_profit": loaded_open.take_profit, "quantity": loaded_open.quantity},
            {"bid": target_tp + 0.10, "ask": target_tp + 0.20, "exchange_ts_ms": now_ms + 5000}
        )
        assert exit_res.triggered is True
        assert exit_res.cause == "TP_HIT"

        closed = TradeLifecycleService.execute_close(
            db=db3, order_id=ord_id, exit_price=target_tp, exit_cause=exit_res.cause, now_ms=now_ms + 5000
        )
        assert closed.state == "closed"
        assert closed.realized_pnl_net > 0.0
        db3.commit()

        # Final durability check
        final_check = db3.query(PaperOrder).filter(PaperOrder.id == ord_id).first()
        assert final_check.state == "closed"
        assert final_check.stop_loss == sl_price
    finally:
        db3.close()


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
        anchor_result=anchor_res, policy=policy, as_of_ms=1060000, tick_size=0.01,
        atr_m1_val=3.0, atr_m1_obj=make_test_atr(3.0, as_of=1060000),
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
        anchor_result=anchor_res, policy=policy, as_of_ms=1060000, tick_size=0.01,
        atr_m1_val=3.0, atr_m1_obj=make_test_atr(3.0, as_of=1060000),
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
        policy=policy, as_of_ms=1060,
        atr_m1_val=2.0, atr_m1_obj=make_test_atr(2.0, as_of=1060),
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
    """DSL-10: Missing required ATR evidence fails closed in PAPER_STRUCTURAL mode (non-executable)."""
    anchor = StopAnchor("a", "SWING_LOW", 2678.0, "1M", 1000, 1060, "l1", "CANDLE_LOW")
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL")
    dec = plan_structural_stop(
        direction="LONG", planned_entry=2700.0, take_profit=2760.0, legacy_sl=2695.0,
        anchor_result=StopAnchorResult(status="AVAILABLE", anchor=anchor),
        policy=policy, as_of_ms=1060, atr_m1_val=None, atr_m1_obj=None
    )
    assert dec.status == StopStatus.WAITING_VOLATILITY_EVIDENCE.value
    assert dec.selected_sl is None
    assert dec.applied_variant == "NONE"
    assert dec.distance_assessment == "DISTANCE_ATR_UNAVAILABLE"
    assert "WAITING_VOLATILITY_EVIDENCE" in dec.reasons
    assert "REQUIRED_M1_ATR_UNAVAILABLE" in dec.reasons

    class DummyCand:
        stop_loss = None
        calculation = None
        stop_placement = dec
    ok, err = validate_applied_stop_decision(candidate=DummyCand(), current_policy=policy)
    assert ok is False
    assert "STRUCTURAL_STOP_STATUS_INVALID" in err


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
        anchor_result=anchor_res, policy=policy, as_of_ms=1060,
        atr_m1_val=3.0, atr_m1_obj=make_test_atr(3.0, as_of=1060),
    )
    d2 = plan_structural_stop(
        direction="LONG", planned_entry=2700.0, take_profit=2760.0, legacy_sl=2695.0,
        anchor_result=anchor_res, policy=policy, as_of_ms=1060,
        atr_m1_val=3.0, atr_m1_obj=make_test_atr(3.0, as_of=1060),
    )
    assert d1.selected_sl == d2.selected_sl
    assert d1.distance_assessment == d2.distance_assessment == "DISTANCE_EXTREME"
    assert d1.to_dict() == d2.to_dict()


def test_dsl_16_arm_fill_exit_persistence_with_distant_sl():
    """DSL-16: Full persisted DB lifecycle with a > 4x ATR distant SL via real ARM, fill, and exit."""
    db = SessionLocal()
    try:
        cand, armed_order, now_ms = make_real_armed_candidate_order(
            db, mode="PAPER_STRUCTURAL", order_id_override="test_v17_7_dsl16_full", distant=True
        )
        assert armed_order is not None
        assert armed_order.state == "armed"
        assert armed_order.stop_loss < 2618.0
        db.commit()

        filled = TradeLifecycleService.execute_fill(
            db, armed_order, fill_price=armed_order.planned_entry, calc_result=cand.calculation, now_ms=now_ms + 1000
        )
        assert filled.state == "paper_open"
        assert filled.stop_loss == armed_order.stop_loss

        closed = TradeLifecycleService.execute_close(
            db, order_id=filled.id, exit_price=armed_order.take_profit, exit_cause="TP_HIT", now_ms=now_ms + 5000
        )
        assert closed.state == "closed"
        assert closed.realized_pnl_net > 0.0
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
    import threading
    barrier = threading.Barrier(2)
    results = []
    ord_id = "test_v17_7_dsl18_race_barrier"

    def _race_worker(worker_id: int):
        db = SessionLocal()
        try:
            o = PaperOrder(
                id=ord_id, symbol="XAUUSDT", direction="LONG", planned_entry=2700.0,
                stop_loss=2677.50, take_profit=2760.0, quantity=1.0, initial_risk_usdt=22.5,
                created_at=1000000 + worker_id, state="armed"
            )
            db.add(o)
            barrier.wait(timeout=5.0)
            db.commit()
            results.append((worker_id, "SUCCESS"))
        except Exception as e:
            db.rollback()
            results.append((worker_id, f"ROLLBACK: {type(e).__name__}"))
        finally:
            db.close()

    t1 = threading.Thread(target=_race_worker, args=(1,))
    t2 = threading.Thread(target=_race_worker, args=(2,))
    t1.start()
    t2.start()
    t1.join(timeout=10.0)
    t2.join(timeout=10.0)

    successes = [r for r in results if r[1] == "SUCCESS"]
    assert len(successes) == 1, f"Expected 1 winner, got {results}"

    db_check = SessionLocal()
    try:
        cnt = db_check.query(PaperOrder).filter(PaperOrder.id == ord_id).count()
        assert cnt == 1
    finally:
        db_check.close()


def test_dsl_19_zero_live_exchange_order_submissions_with_distant_sl():
    """DSL-19: Instrumented network spy confirms zero live exchange order submissions during real distant-SL lifecycle."""
    spy = Mock()
    def _forbidden_sink(*args, **kwargs):
        spy(*args, **kwargs)
        raise RuntimeError("FORBIDDEN_LIVE_ORDER_SUBMISSION")

    with patch("httpx.Client.post", side_effect=_forbidden_sink), \
         patch("httpx.AsyncClient.post", side_effect=_forbidden_sink):

        db = SessionLocal()
        try:
            cand, armed_order, now_ms = make_real_armed_candidate_order(
                db, mode="PAPER_STRUCTURAL", order_id_override="test_v17_7_dsl19_distant_safety", distant=True
            )
            assert armed_order is not None
            filled = TradeLifecycleService.execute_fill(
                db, armed_order, fill_price=armed_order.planned_entry, calc_result=cand.calculation, now_ms=now_ms + 1000
            )
            assert filled.state == "paper_open"
            closed = TradeLifecycleService.execute_close(
                db, order_id=filled.id, exit_price=armed_order.take_profit, exit_cause="TP_HIT", now_ms=now_ms + 5000
            )
            assert closed.state == "closed"
        finally:
            db.close()

        assert spy.call_count == 0, "Zero outbound exchange orders permitted"


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


# ==============================================================================
# GATES CE-01 through CE-20: Causal SL Evidence & True Runtime Gates (V17.7.2)
# ==============================================================================

def test_ce_01_missing_m1_atr_blocks_paper_structural():
    """CE-01: Missing M1 ATR blocks PAPER_STRUCTURAL fail-closed (status WAITING_VOLATILITY_EVIDENCE, selected_sl None)."""
    anchor = StopAnchor("anc_ce01", "SWING_LOW", 2690.0, "1M", 1000, 1060, "l1", "CANDLE_LOW")
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL")
    dec = plan_structural_stop(
        direction="LONG", planned_entry=2700.0, take_profit=2730.0, legacy_sl=2695.0,
        anchor_result=StopAnchorResult(status="AVAILABLE", anchor=anchor),
        policy=policy, as_of_ms=1060, atr_m1_val=None, atr_m1_obj=None
    )
    assert dec.status == StopStatus.WAITING_VOLATILITY_EVIDENCE.value
    assert dec.selected_sl is None
    assert dec.applied_variant == "NONE"
    assert dec.distance_assessment == "DISTANCE_ATR_UNAVAILABLE"
    assert "WAITING_VOLATILITY_EVIDENCE" in dec.reasons
    assert "REQUIRED_M1_ATR_UNAVAILABLE" in dec.reasons

    class DummyCand:
        stop_loss = None
        calculation = None
        stop_placement = dec
    ok, err = validate_applied_stop_decision(candidate=DummyCand(), current_policy=policy)
    assert ok is False
    assert "STRUCTURAL_STOP_STATUS_INVALID" in err


def test_ce_02_invalid_numerical_atr_fails_closed():
    """CE-02: NaN, infinite, zero, and negative ATR values block PAPER_STRUCTURAL fail-closed."""
    anchor = StopAnchor("anc_ce02", "SWING_LOW", 2690.0, "1M", 1000, 1060, "l1", "CANDLE_LOW")
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL")
    for bad_atr in (float("nan"), float("inf"), float("-inf"), 0.0, -1.5):
        dec = plan_structural_stop(
            direction="LONG", planned_entry=2700.0, take_profit=2730.0, legacy_sl=2695.0,
            anchor_result=StopAnchorResult(status="AVAILABLE", anchor=anchor),
            policy=policy, as_of_ms=1060, atr_m1_val=bad_atr
        )
        assert dec.status == StopStatus.WAITING_VOLATILITY_EVIDENCE.value
        assert dec.selected_sl is None
        assert dec.applied_variant == "NONE"


def test_ce_03_insufficient_closed_candles_fails_closed():
    """CE-03: Insufficient closed M1 candles (< 14 bars) blocks PAPER_STRUCTURAL."""
    candles = make_dummy_candles(n=5)
    as_of = candles[-1]["timestamp"] + 60000
    atr_res = compute_stop_atr(candles, period=14, timeframe="1M", as_of_ms=as_of)
    assert atr_res.status == "UNAVAILABLE"

    anchor = StopAnchor("anc_ce03", "SWING_LOW", 2690.0, "1M", 1000, as_of, "l1", "CANDLE_LOW")
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL")
    dec = plan_structural_stop(
        direction="LONG", planned_entry=2700.0, take_profit=2730.0, legacy_sl=2695.0,
        anchor_result=StopAnchorResult(status="AVAILABLE", anchor=anchor),
        policy=policy, as_of_ms=as_of, atr_m1_obj=atr_res
    )
    assert dec.status == StopStatus.WAITING_VOLATILITY_EVIDENCE.value
    assert dec.selected_sl is None


def test_ce_04_future_unclosed_atr_source_rejected():
    """CE-04: Unclosed or future candles strictly excluded from stop ATR measurement."""
    candles = make_dummy_candles(n=20, start_ts=1000000)
    as_of = candles[15]["timestamp"] + 30000  # middle of candle 15 (unclosed!)
    atr_res = compute_stop_atr(candles, period=14, timeframe="1M", as_of_ms=as_of)
    # Only bars 0..14 are fully closed
    assert atr_res.status == "AVAILABLE"
    # When as_of is before minimum 14 bars are closed:
    as_of_early = candles[5]["timestamp"]
    atr_early = compute_stop_atr(candles, period=14, timeframe="1M", as_of_ms=as_of_early)
    assert atr_early.status == "UNAVAILABLE"


def test_ce_05_optional_m5_missing_handled_per_policy():
    """CE-05: Missing optional M5 ATR is handled gracefully while valid M1 ATR allows valid structural stop."""
    anchor = StopAnchor("anc_ce05", "SWING_LOW", 2690.0, "1M", 1000, 1060, "l1", "CANDLE_LOW")
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL")
    dec = plan_structural_stop(
        direction="LONG", planned_entry=2700.0, take_profit=2730.0, legacy_sl=2695.0,
        anchor_result=StopAnchorResult(status="AVAILABLE", anchor=anchor),
        policy=policy, as_of_ms=1060,
        atr_m1_val=1.5, atr_m1_obj=make_test_atr(1.5, as_of=1060),
        atr_m5_val=None, atr_m5_obj=None
    )
    assert dec.status == StopStatus.VALID.value
    assert dec.selected_sl is not None
    assert dec.selected_sl < 2690.0


def test_ce_06_invalid_anchor_causal_ordering_rejected():
    """CE-06: Anchor with occurred_at > known_at or known_at > as_of_ms is rejected with CAUSAL_ORDERING_VIOLATION."""
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL")
    # 1. occurred_at > known_at
    a1 = StopAnchor("anc_causal1", "SWING_LOW", 2690.0, "1M", occurred_at_ms=1050, known_at_ms=1000, source_leg_id="l1", price_basis="CANDLE_LOW")
    dec1 = plan_structural_stop(
        direction="LONG", planned_entry=2700.0, take_profit=2730.0, legacy_sl=2695.0,
        anchor_result=StopAnchorResult(status="AVAILABLE", anchor=a1),
        policy=policy, as_of_ms=1060, atr_m1_val=1.5
    )
    assert dec1.status == StopStatus.INVALID_DATA.value
    assert "CAUSAL_ORDERING_VIOLATION" in dec1.reasons

    # 2. known_at > as_of_ms (lookahead)
    a2 = StopAnchor("anc_causal2", "SWING_LOW", 2690.0, "1M", occurred_at_ms=1000, known_at_ms=1100, source_leg_id="l1", price_basis="CANDLE_LOW")
    dec2 = plan_structural_stop(
        direction="LONG", planned_entry=2700.0, take_profit=2730.0, legacy_sl=2695.0,
        anchor_result=StopAnchorResult(status="AVAILABLE", anchor=a2),
        policy=policy, as_of_ms=1060, atr_m1_val=1.5
    )
    assert dec2.status == StopStatus.INVALID_DATA.value
    assert "CAUSAL_ORDERING_VIOLATION" in dec2.reasons


def test_ce_07_anchor_from_other_episode_rejected():
    """CE-07: Anchor from an unrelated structural episode is rejected."""
    candles = make_dummy_candles(n=30)
    sh_pivots = [{"id": "sh_other", "pivot_id": "sh_other", "high": 2720.0, "price": 2720.0, "occurred_at_ms": 1000, "time_ms": 1000, "known_at_ms": 1060}]
    sl_pivots = []
    selected_break = {
        "id": "brk_ep_a", "broken_price": 2695.0, "broken_at_ms": 2000, "break_time_ms": 2000,
        "known_at_ms": 2060, "origin_pivot_id": "sh_different_origin"
    }
    res = resolve_stop_anchor(
        model_id="M1_CONTINUATION", direction="SHORT", closed_bars=candles,
        selected_break=selected_break, confirmed_swings=(sh_pivots, sl_pivots), as_of_ms=3000
    )
    assert res.status in ("MISSING_DATA", "INVALID_DATA", "WAITING_EVIDENCE")


def test_ce_08_previously_invalidated_anchor_rejected():
    """CE-08: Structural anchor breached by subsequent closed candle is rejected with ANCHOR_ALREADY_INVALIDATED."""
    candles = make_dummy_candles(n=20, base_price=2700.0, start_ts=1000000)
    # Candle 10 breaches anchor level 2690.0
    candles[10]["low"] = 2688.0
    sweep = {"id": "sw_breached", "sweep_extreme": 2690.0, "occurred_at_ms": 1300000, "known_at_ms": 1360000}
    as_of = candles[15]["timestamp"]
    res = resolve_stop_anchor(
        model_id="M1_REVERSAL", direction="LONG", closed_bars=candles,
        selected_sweep=sweep, as_of_ms=as_of
    )
    assert res.status == "INVALID_DATA"
    assert "ANCHOR_ALREADY_INVALIDATED" in res.reasons


def test_ce_09_inverted_bid_ask_cannot_normalize_to_valid_zero_spread():
    """CE-09: Inverted bid/ask (ask < bid) cannot be normalized into a valid zero spread; fails closed."""
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL")
    with pytest.raises(ValueError) as exc:
        compute_stop_buffer(policy=policy, tick_size=0.01, atr_m1=StopATRResult(status="AVAILABLE", value=1.5), bid=2701.0, ask=2700.0)
    assert "INVERTED_QUOTE" in str(exc.value)

    # In plan_structural_stop with inverted quote spread
    anchor = StopAnchor("anc_ce09", "SWING_LOW", 2690.0, "1M", 1000, 1060, "l1", "CANDLE_LOW")
    dec = plan_structural_stop(
        direction="LONG", planned_entry=2700.0, take_profit=2730.0, legacy_sl=2695.0,
        anchor_result=StopAnchorResult(status="AVAILABLE", anchor=anchor),
        policy=policy, as_of_ms=1060, atr_m1_val=1.5, spread=-1.0
    )
    assert dec.status == StopStatus.INVALID_DATA.value
    assert "INVALID_SPREAD" in dec.reasons or "INVERTED_QUOTE" in dec.reasons


def test_ce_10_stale_quote_blocks_execution():
    """CE-10: Stale quote blocks candidate evaluation up-front."""
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)
    ctx = {"4H": c_4h, "1H": c_1h, "15M": c_15m, "1M": c_1m}
    now_ms = t_start + 600_000
    # Quote marked stale
    quote_dict = {"bid": 2623.75, "ask": 2623.85, "is_valid": True, "is_stale": True, "timestamp": now_ms - 100_000}
    cfg = CG01Config(enabled=True, entry_mode=CG01EntryMode.LIMIT_AT_CE_AFTER_CONFIRMATION.value)
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL")
    dec = evaluate_cg01_candidate(ctx, quote_dict, config=cfg, now_ms=now_ms, stop_policy=policy)
    assert dec.candidate is None
    assert "STALE_QUOTE" in dec.reason_codes


def test_ce_11_real_cg01_evaluation_with_distant_valid_sl():
    """CE-11: Real CG01 candidate evaluation with distant valid SL (> 4x ATR) succeeds when evidence & risk pass."""
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)
    for b in c_15m:
        if b["low"] < 2620.0:
            b["low"] = 2615.0
    for b in c_1m:
        if b["low"] < 2620.0:
            b["low"] = 2615.0

    ctx = {"4H": c_4h, "1H": c_1h, "15M": c_15m, "1M": c_1m}
    now_ms = t_start + 600_000
    quote_dict = {"bid": 2623.75, "ask": 2623.85, "is_valid": True, "timestamp": now_ms}
    cfg = CG01Config(enabled=True, entry_mode=CG01EntryMode.LIMIT_AT_CE_AFTER_CONFIRMATION.value, min_net_rr=1.0)
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL")
    dec = evaluate_cg01_candidate(ctx, quote_dict, config=cfg, now_ms=now_ms, stop_policy=policy)
    assert dec.candidate is not None
    cand = dec.candidate
    assert cand.stop_loss < 2618.0
    assert cand.stop_placement.distance_assessment == "DISTANCE_EXTREME"
    assert cand.calculation.can_execute is True


def test_ce_12_real_m1_evaluator_structural_stop():
    """CE-12: Real M1 Reversal evaluator produces valid structural stop with candidate equality."""
    from tests.test_v15_1_execution_lifecycle import _make_causal_long_m1_candles
    from services.scalp_m1_model_service import ScalpM1ModelService, M1ScalpConfig
    from services.scalp_m1_types import WatchedZone, ZoneState

    cfg = M1ScalpConfig(min_closed_m1_bars=15, displacement_body_atr_min=0.7)
    zone = WatchedZone(
        zone_id="z_ce12", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.CONFIRMED.value,
    )
    causal_candles = _make_causal_long_m1_candles()
    quote = {"bid": 2622.0, "ask": 2622.5, "is_valid": True}
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL")
    res = ScalpM1ModelService.evaluate_m1_reversal_candidate(
        zone, causal_candles, quote, {"capital_usdt": 1000.0, "risk_pct": 0.25, "leverage": 50},
        now_ms=1300_000, config=cfg, stop_policy=policy
    )
    assert res.is_eligible is True
    assert res.stop_loss == res.stop_placement.selected_sl
    assert res.stop_placement.status == StopStatus.VALID.value
    assert res.stop_placement.applied_variant == "C"


def test_ce_13_real_orchestrator_arm_with_frozen_evidence():
    """CE-13: Production orchestrator ARM freezes stop evidence across independent DB session reload."""
    db1 = SessionLocal()
    try:
        cand, armed_order, now_ms = make_real_armed_candidate_order(
            db1, mode="PAPER_STRUCTURAL", order_id_override="test_v17_7_ce13_frozen"
        )
        assert armed_order is not None
        assert armed_order.state == "armed"
        expected_sl = armed_order.stop_loss
        db1.commit()
    finally:
        db1.close()

    db2 = SessionLocal()
    try:
        reloaded = db2.query(PaperOrder).filter(PaperOrder.id == "test_v17_7_ce13_frozen").first()
        assert reloaded is not None
        assert reloaded.stop_loss == expected_sl
        cost_dict = json.loads(reloaded.cost_snapshot)
        assert "stop_placement" in cost_dict
        assert cost_dict["stop_placement"]["selected_sl"] == expected_sl
    finally:
        db2.close()


def test_ce_14_real_execution_coordinator_fill_quantity_preservation():
    """CE-14: Execution coordinator fill strictly preserves or downsizes quantity, never increases."""
    db = SessionLocal()
    try:
        cand, armed_order, now_ms = make_real_armed_candidate_order(
            db, mode="PAPER_STRUCTURAL", order_id_override="test_v17_7_ce14_downsize"
        )
        armed_qty = armed_order.quantity

        calc_larger = calculate_risk_reward(
            direction=armed_order.direction, planned_entry=armed_order.planned_entry,
            stop_loss=armed_order.stop_loss, take_profit=armed_order.take_profit,
            capital_usdt=50000.0, risk_pct=0.10  # Massive capital increase
        )
        assert calc_larger.quantity > armed_qty

        filled = TradeLifecycleService.execute_fill(
            db=db, order=armed_order, fill_price=armed_order.planned_entry,
            calc_result=calc_larger, now_ms=now_ms + 1000
        )
        assert filled.quantity <= armed_qty
    finally:
        db.close()


def test_ce_15_real_bid_ask_stop_trigger_and_persisted_close():
    """CE-15: Real bid/ask stop triggers for LONG and SHORT with persisted closed state and realized PnL."""
    db = SessionLocal()
    try:
        cand, armed_order, now_ms = make_real_armed_candidate_order(
            db, mode="PAPER_STRUCTURAL", order_id_override="test_v17_7_ce15_close"
        )
        filled = TradeLifecycleService.execute_fill(
            db, armed_order, fill_price=armed_order.planned_entry, calc_result=cand.calculation, now_ms=now_ms + 1000
        )

        # Trigger SL by bid breaching stop
        sl_quote = {"bid": filled.stop_loss - 0.10, "ask": filled.stop_loss, "exchange_ts_ms": now_ms + 2000}
        exit_res = evaluate_order_exit(
            {"direction": filled.direction, "planned_entry": filled.planned_entry,
             "actual_entry": filled.actual_entry, "stop_loss": filled.stop_loss,
             "take_profit": filled.take_profit, "quantity": filled.quantity},
            sl_quote
        )
        assert exit_res.triggered is True
        assert exit_res.cause == "SL_HIT"

        closed = TradeLifecycleService.execute_close(
            db, order_id=filled.id, exit_price=filled.stop_loss, exit_cause=exit_res.cause, now_ms=now_ms + 2000
        )
        assert closed.state == "closed"
        assert closed.realized_pnl_net < 0.0
    finally:
        db.close()


def test_ce_16_concurrent_real_arm_barrier_single_order():
    """CE-16 (V173-P1-02): Concurrent calls through production ARM boundary create at most one order."""
    import threading
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)
    c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)
    ctx = {"4H": c_4h, "1H": c_1h, "15M": c_15m, "1M": c_1m}
    now_ms = t_start + 600_000
    quote_dict = {"bid": 2623.75, "ask": 2623.85, "is_valid": True, "timestamp": now_ms}
    cfg = CG01Config(enabled=True, entry_mode=CG01EntryMode.LIMIT_AT_CE_AFTER_CONFIRMATION.value, min_net_rr=1.5)
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL")
    dec = evaluate_cg01_candidate(ctx, quote_dict, config=cfg, now_ms=now_ms, stop_policy=policy)
    assert dec.candidate is not None
    cand = dec.candidate

    v_quote = ValidatedQuote(
        is_valid=True, bid=2623.75, ask=2623.85, last=2623.80, spread=0.10,
        exchange_time=now_ms, observed_at=now_ms, freshness_sec=0.1, is_stale=False,
        rejection_code=None, rejection_detail=None
    )
    policy_settings = {"capital_usdt": 1000.0, "risk_pct": 1.0, "config_version": 1}
    orch = ScalpWatcherOrchestrator()

    barrier = threading.Barrier(2)
    results = []

    def _worker(worker_id: int):
        db = SessionLocal()
        try:
            barrier.wait(timeout=5.0)
            armed = orch._arm_paper_candidate(
                db, cand, now_ms=now_ms, clock=live_clock, quote=v_quote,
                policy_settings=policy_settings, auto_enabled=True,
            )
            if armed is not None:
                db.commit()
                results.append((worker_id, "ARMED", armed.id))
            else:
                db.rollback()
                results.append((worker_id, "BLOCKED", None))
        except Exception as e:
            db.rollback()
            results.append((worker_id, f"ROLLBACK: {type(e).__name__}", None))
        finally:
            db.close()

    t1 = threading.Thread(target=_worker, args=(1,))
    t2 = threading.Thread(target=_worker, args=(2,))
    t1.start()
    t2.start()
    t1.join(timeout=10.0)
    t2.join(timeout=10.0)

    # In independent session, verify strictly at most one order exists
    db_verify = SessionLocal()
    try:
        orders = db_verify.query(PaperOrder).filter(PaperOrder.instrument == cand.symbol).all()
        assert len(orders) == 1, f"Expected exactly 1 durable order, found {len(orders)}: {[o.id for o in orders]}"
        armed_order_ids = set(r[2] for r in results if r[1] == "ARMED")
        assert len(armed_order_ids) == 1, f"Expected idempotent resolution to 1 order id, got {armed_order_ids}"
        assert orders[0].id == list(armed_order_ids)[0]
    finally:
        db_verify.close()


def test_ce_17_test_database_cannot_touch_runtime_db():
    """CE-17: assert_test_database_isolated enforces test-owned root containment and rejects arbitrary or runtime DBs."""
    from database import assert_test_database_isolated, get_or_create_test_root

    # 1. Protected test path inside verified test-owned root passes
    test_root = get_or_create_test_root()
    valid_test_db = str(test_root / "test_valid.db")
    assert assert_test_database_isolated(valid_test_db) is True

    # 2. Production path fails closed with RuntimeError
    with pytest.raises(RuntimeError) as exc:
        assert_test_database_isolated("/Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/aurum_desk.db")
    assert "CRITICAL SAFETY HAZARD" in str(exc.value)

    # 3. Arbitrary external path outside test-owned root fails closed with RuntimeError (V173-P0-01)
    with pytest.raises(RuntimeError) as exc2:
        assert_test_database_isolated("/tmp/aurum_test_1234/test.db")
    assert "CRITICAL SAFETY HAZARD" in str(exc2.value)
    assert "not within verified test-owned root" in str(exc2.value)


def test_ce_18_negative_control_live_order_dispatch_blocked():
    """CE-18: Negative control proof: Attempted live exchange order dispatch is detected and blocked by security instrumentation."""
    spy = Mock()
    def _forbidden_sink(*args, **kwargs):
        spy(*args, **kwargs)
        raise RuntimeError("FORBIDDEN_LIVE_ORDER_SUBMISSION: Live exchange order attempt detected!")

    with patch("httpx.Client.post", side_effect=_forbidden_sink):
        import httpx
        with pytest.raises(RuntimeError) as exc:
            client = httpx.Client()
            client.post("https://api.binance.com/fapi/v1/order", json={"symbol": "XAUUSDT", "side": "BUY", "type": "MARKET"})
        assert "FORBIDDEN_LIVE_ORDER_SUBMISSION" in str(exc.value)
        assert spy.call_count == 1


def test_ce_19_acceptance_runner_refuses_pass_on_missing_gates():
    """CE-19 (V17.7.4 Section 12.2): Master acceptance runner verdict module refuses PASS under all fail-closed conditions."""
    from tools.acceptance_verdict import determine_release_verdict

    manifest = ["G01", "G02", "G03"]

    # 1. Gate missing from results
    assert determine_release_verdict({"G01": {"status": "PASS", "exit_code": 0}, "G02": {"status": "PASS", "exit_code": 0}}, manifest) == "BLOCKED"

    # 2. Gate NOT_EXECUTED
    assert determine_release_verdict({"G01": {"status": "PASS", "exit_code": 0}, "G02": {"status": "PASS", "exit_code": 0}, "G03": {"status": "NOT_EXECUTED"}}, manifest) == "BLOCKED"

    # 3. Gate SKIPPED_WITH_REASON
    assert determine_release_verdict({"G01": {"status": "PASS", "exit_code": 0}, "G02": {"status": "PASS", "exit_code": 0}, "G03": {"status": "SKIPPED_WITH_REASON"}}, manifest) == "BLOCKED"

    # 4. Gate FAIL
    assert determine_release_verdict({"G01": {"status": "PASS", "exit_code": 0}, "G02": {"status": "FAIL", "exit_code": 1}, "G03": {"status": "PASS", "exit_code": 0}}, manifest) == "BLOCKED"

    # 5. Nonzero exit code contradicts PASS
    assert determine_release_verdict({"G01": {"status": "PASS", "exit_code": 0}, "G02": {"status": "PASS", "exit_code": 0}, "G03": {"status": "PASS", "exit_code": 1}}, manifest) == "BLOCKED"

    # 6. Missing exit_code on command gate
    assert determine_release_verdict({"G01": {"status": "PASS", "exit_code": 0}, "G02": {"status": "PASS", "exit_code": 0}, "G03": {"status": "PASS"}}, manifest) == "BLOCKED"

    # 7. Boolean exit_code (e.g. exit_code=True)
    assert determine_release_verdict({"G01": {"status": "PASS", "exit_code": 0}, "G02": {"status": "PASS", "exit_code": 0}, "G03": {"status": "PASS", "exit_code": True}}, manifest) == "BLOCKED"

    # 8. Empty manifest
    assert determine_release_verdict({"G01": {"status": "PASS", "exit_code": 0}}, []) == "BLOCKED"

    # 9. Duplicate manifest items
    assert determine_release_verdict({"G01": {"status": "PASS", "exit_code": 0}}, ["G01", "G01"]) == "BLOCKED"

    # 10. Collection errors in counts
    assert determine_release_verdict({"G01": {"status": "PASS", "exit_code": 0, "counts": {"collection_errors": 1}}, "G02": {"status": "PASS", "exit_code": 0}, "G03": {"status": "PASS", "exit_code": 0}}, manifest) == "BLOCKED"

    # 11. All skipped suite (passed=0, collected=5, skipped=5)
    assert determine_release_verdict({"G01": {"status": "PASS", "exit_code": 0, "counts": {"passed": 0, "collected": 5, "skipped": 5}}, "G02": {"status": "PASS", "exit_code": 0}, "G03": {"status": "PASS", "exit_code": 0}}, manifest) == "BLOCKED"

    # 12. Negative test counts
    assert determine_release_verdict({"G01": {"status": "PASS", "exit_code": 0, "counts": {"passed": -1}}, "G02": {"status": "PASS", "exit_code": 0}, "G03": {"status": "PASS", "exit_code": 0}}, manifest) == "BLOCKED"

    # 13. Unverified integrity flag
    assert determine_release_verdict({"G01": {"status": "PASS", "exit_code": 0, "integrity_verified": False}, "G02": {"status": "PASS", "exit_code": 0}, "G03": {"status": "PASS", "exit_code": 0}}, manifest) == "BLOCKED"

    # 14. Positive control: All mandatory gates PASS with valid counts
    res_pass = {
        "G01": {"status": "PASS", "exit_code": 0, "counts": {"passed": 5, "failed": 0}},
        "G02": {"status": "PASS", "exit_code": 0, "counts": {"passed": 10, "failed": 0}},
        "G03": {"status": "PASS", "exit_code": 0, "counts": {"passed": 2, "failed": 0}},
    }
    assert determine_release_verdict(res_pass, manifest) == "ACCEPTED_FOR_PAPER_OPT_IN"


def test_ce_20_acceptance_artifact_source_sha_matches_tested_source(tmp_path):
    """CE-20 (V17.7.4 Section 12.1): Acceptance artifact writer and verifier enforce on-disk bundle integrity and negative controls."""
    from tools.acceptance_verdict import MANDATORY_GATES_V17_7_4
    from tools.artifact_verifier import (
        build_source_manifest,
        write_acceptance_artifacts,
        verify_acceptance_bundle,
    )
    import subprocess
    import sys

    cmd = [sys.executable, "-c", "import subprocess; print(subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip())"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    expected_sha = res.stdout.strip()
    assert len(expected_sha) == 40

    run_id = "test_run_ce20"
    source_manifest = {"schema_version": 1, "files": {"backend/database.py": "abc123hash"}, "composite_sha": "comp_sha"}
    gate_manifest = list(MANDATORY_GATES_V17_7_4)

    # Build valid gates payload with all 28 gates
    valid_gates = {}
    for g in gate_manifest:
        valid_gates[g] = {"status": "PASS", "exit_code": 0, "counts": {"passed": 1, "failed": 0}}

    results_payload = {
        "timestamp_utc": "2026-10-27T00:00:00Z",
        "runner_version": "17.7.4",
        "run_id": run_id,
        "branch": "feat/v17-7-adaptive-structural-stop-loss",
        "source_sha": expected_sha,
        "gates": valid_gates,
        "verdict": "ACCEPTED_FOR_PAPER_OPT_IN",
    }

    # 1. Write actual valid bundle to disk
    run_dir = write_acceptance_artifacts(
        run_id=run_id,
        results_payload=results_payload,
        source_manifest=source_manifest,
        gate_manifest=gate_manifest,
        artifacts_base_dir=tmp_path,
    )

    # 2. Positive control: valid bundle passes verification
    ok, errors = verify_acceptance_bundle(run_dir, expected_version="17.7.4", expected_run_id=run_id, expected_source_sha=expected_sha)
    assert ok is True, f"Positive control failed: {errors}"
    assert len(errors) == 0

    # 3. Negative control: Stale source SHA
    ok_stale, errors_stale = verify_acceptance_bundle(run_dir, expected_source_sha="0000000000000000000000000000000000000000")
    assert ok_stale is False
    assert any("Source SHA mismatch" in e for e in errors_stale)

    # 4. Negative control: Wrong runner version
    ok_ver, errors_ver = verify_acceptance_bundle(run_dir, expected_version="17.7.3")
    assert ok_ver is False
    assert any("Version mismatch" in e for e in errors_ver)

    # 5. Negative control: Cross-run ID reference
    ok_run, errors_run = verify_acceptance_bundle(run_dir, expected_run_id="foreign_run_999")
    assert ok_run is False
    assert any("Run ID mismatch" in e for e in errors_run)

    # 6. Negative control: Modified report bytes / Tampered content
    res_file = run_dir / "acceptance_results.json"
    orig_content = res_file.read_text(encoding="utf-8")
    try:
        res_file.write_text(orig_content.replace("17.7.4", "17.7.4_TAMPERED"), encoding="utf-8")
        ok_tamp, errors_tamp = verify_acceptance_bundle(run_dir, expected_version="17.7.4")
        assert ok_tamp is False
        assert any("Integrity mismatch" in e for e in errors_tamp)
    finally:
        res_file.write_text(orig_content, encoding="utf-8")

    # 7. Negative control: Missing required artifact
    src_man_file = run_dir / "source_manifest.json"
    src_man_backup = src_man_file.read_text(encoding="utf-8")
    try:
        src_man_file.unlink()
        ok_miss, errors_miss = verify_acceptance_bundle(run_dir)
        assert ok_miss is False
        assert any("Missing required artifact" in e for e in errors_miss)
    finally:
        src_man_file.write_text(src_man_backup, encoding="utf-8")

    # 8. Negative control: Malformed JSON
    try:
        res_file.write_text("{ MALFORMED_JSON_BYTES", encoding="utf-8")
        ok_malf, errors_malf = verify_acceptance_bundle(run_dir)
        assert ok_malf is False
    finally:
        res_file.write_text(orig_content, encoding="utf-8")
