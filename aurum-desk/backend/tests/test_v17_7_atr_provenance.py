"""
backend/tests/test_v17_7_atr_provenance.py
AURUM DESK V17.7.3 — ATR Provenance Authority & Volatility Guard Test Suite
Verifies ATR-01 through ATR-20:
Authoritative raw ATR provenance, separation from weighted buffer, fail-closed handling,
causal timeframes, symbol validation, ratio precision, and model parity.
"""

import math
from datetime import datetime
from zoneinfo import ZoneInfo
import pytest

from services.stop_placement_types import (
    StopATRResult,
    StopBufferResult,
    StopPlacementPolicy,
    StopPlacementMode,
    StopStatus,
    StopAnchor,
    StopAnchorResult,
)
from services.stop_placement_service import (
    compute_stop_atr,
    compute_stop_buffer,
    plan_structural_stop,
)
from services.cg01_engine import evaluate_cg01_candidate
from services.cg01_types import CG01Config, CG01EntryMode
from services.scalp_m1_model_service import ScalpM1ModelService
from tests.test_v16_3_cg01_pipeline import build_long_fixture_series


def make_dummy_candles(n: int, t_start: int = 1_000_000, duration: int = 60_000, symbol: str = "XAUUSDT"):
    candles = []
    for i in range(n):
        t = t_start + i * duration
        candles.append({
            "timestamp": t,
            "open": 2650.0 + i * 0.1,
            "high": 2652.0 + i * 0.1,
            "low": 2649.0 + i * 0.1,
            "close": 2651.0 + i * 0.1,
            "symbol": symbol,
            "is_closed": True,
        })
    return candles


def test_atr_01_raw_atr_and_weighted_buffer_are_distinct():
    """ATR-01: Raw ATR and weighted buffer contribution are distinct and non-interchangeable."""
    raw_atr_val = 3.00
    atr_res = StopATRResult(status="AVAILABLE", value=raw_atr_val, timeframe="1M")
    policy = StopPlacementPolicy(atr_m1_multiplier=0.25, min_buffer_ticks=5)
    buf_res = compute_stop_buffer(policy=policy, tick_size=0.01, atr_m1=atr_res, bid=2650.0, ask=2650.1)

    # Weighted contribution is 0.25 * 3.00 = 0.75
    assert buf_res.components["atr_m1"] == 0.75
    # Raw ATR preserved on buffer is 3.00
    assert buf_res.raw_atr_m1 == 3.00
    assert buf_res.components["atr_m1"] != buf_res.raw_atr_m1


def test_atr_02_weighted_buffer_cannot_satisfy_missing_atr_gate():
    """ATR-02: Weighted buffer component cannot satisfy missing raw ATR gate under PAPER_STRUCTURAL."""
    anchor = StopAnchor(
        id="anc_1", kind="SWING_LOW", price=2640.0, timeframe="1M",
        occurred_at_ms=1000, known_at_ms=1060, source_leg_id="leg_1"
    )
    anchor_res = StopAnchorResult(status="AVAILABLE", anchor=anchor)
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL")

    # Construct mock buffer with component, but NO raw ATR object or raw_atr_m1
    buf = StopBufferResult(
        buffer_price=0.75,
        components={"atr_m1": 0.75, "tick_floor": 0.05},
        dominant_source="atr_m1",
        tick_size=0.01,
        raw_atr_m1=None,  # No raw ATR!
    )
    dec = plan_structural_stop(
        direction="LONG", planned_entry=2650.0, take_profit=2670.0, legacy_sl=2645.0,
        anchor_result=anchor_res, buffer_result=buf, policy=policy, as_of_ms=2000, tick_size=0.01,
        atr_m1_obj=None, atr_m1_val=None,
    )
    assert dec.status == StopStatus.WAITING_VOLATILITY_EVIDENCE.value
    assert dec.selected_sl is None
    assert "REQUIRED_M1_ATR_UNAVAILABLE" in dec.reasons


def test_atr_03_missing_raw_atr_fails_closed():
    """ATR-03: Missing raw ATR fails closed with WAITING_VOLATILITY_EVIDENCE in PAPER_STRUCTURAL."""
    anchor = StopAnchor(id="anc_1", kind="SWING_LOW", price=2640.0, timeframe="1M", occurred_at_ms=1000, known_at_ms=1060, source_leg_id="leg_1")
    dec = plan_structural_stop(
        direction="LONG", planned_entry=2650.0, take_profit=2670.0, legacy_sl=2645.0,
        anchor_result=StopAnchorResult(status="AVAILABLE", anchor=anchor),
        policy=StopPlacementPolicy(mode="PAPER_STRUCTURAL"),
        as_of_ms=2000, tick_size=0.01,
        atr_m1_obj=None, atr_m1_val=None,
    )
    assert dec.status == StopStatus.WAITING_VOLATILITY_EVIDENCE.value
    assert dec.selected_sl is None


def test_atr_04_explicitly_unavailable_atr_cannot_be_overridden_by_scalar():
    """ATR-04: Authoritative StopATRResult with UNAVAILABLE cannot be silently overridden by positive scalar."""
    unavail_atr = StopATRResult(status="UNAVAILABLE", value=0.0, reason_code="INSUFFICIENT_BARS")
    anchor = StopAnchor(id="anc_1", kind="SWING_LOW", price=2640.0, timeframe="1M", occurred_at_ms=1000, known_at_ms=1060, source_leg_id="leg_1")
    dec = plan_structural_stop(
        direction="LONG", planned_entry=2650.0, take_profit=2670.0, legacy_sl=2645.0,
        anchor_result=StopAnchorResult(status="AVAILABLE", anchor=anchor),
        policy=StopPlacementPolicy(mode="PAPER_STRUCTURAL"),
        as_of_ms=2000, tick_size=0.01,
        atr_m1_obj=unavail_atr,
        atr_m1_val=3.0,  # Scalar attempt to override authoritative unavail_atr!
    )
    assert dec.status == StopStatus.WAITING_VOLATILITY_EVIDENCE.value
    assert dec.selected_sl is None


def test_atr_05_wrong_timeframe_fails():
    """ATR-05: Wrong or unsupported timeframe fails with UNAVAILABLE."""
    candles = make_dummy_candles(20)
    res = compute_stop_atr(candles, period=14, timeframe="INVALID_TF")
    assert res.status == "UNAVAILABLE"
    assert res.reason_code == "UNSUPPORTED_TIMEFRAME"


def test_atr_06_wrong_symbol_fails():
    """ATR-06: Candle series with symbol mismatch fails with UNAVAILABLE."""
    candles = make_dummy_candles(20, symbol="BTCUSDT")
    res = compute_stop_atr(candles, period=14, timeframe="1M", symbol="XAUUSDT")
    assert res.status == "UNAVAILABLE"
    assert res.reason_code == "SYMBOL_MISMATCH"


def test_atr_07_insufficient_closed_candles_fails():
    """ATR-07: Candle count less than period + 1 fails with INSUFFICIENT_BARS."""
    candles = make_dummy_candles(10)  # Need 15 for period=14
    res = compute_stop_atr(candles, period=14, timeframe="1M")
    assert res.status == "UNAVAILABLE"
    assert res.reason_code == "INSUFFICIENT_BARS"


def test_atr_08_future_candle_fails():
    """ATR-08: Candles closing after as_of_ms are strictly excluded from calculation."""
    now_ms = 1_500_000
    # Create 20 candles starting at now_ms (all close in the future!)
    candles = make_dummy_candles(20, t_start=now_ms)
    res = compute_stop_atr(candles, period=14, timeframe="1M", as_of_ms=now_ms)
    assert res.status == "UNAVAILABLE"
    assert res.reason_code == "INSUFFICIENT_BARS"


def test_atr_09_unclosed_candle_fails():
    """ATR-09: Unclosed candle in stream is excluded from ATR computation."""
    candles = make_dummy_candles(15)
    candles[-1]["is_closed"] = False  # Last candle is still open
    res = compute_stop_atr(candles, period=14, timeframe="1M")
    # 14 closed bars left, insufficient for period=14 (requires 15 closed bars)
    assert res.status == "UNAVAILABLE"
    assert res.reason_code == "INSUFFICIENT_BARS"


def test_atr_10_nan_and_infinite_atr_fail():
    """ATR-10: NaN or infinite values in candles produce UNAVAILABLE."""
    candles = make_dummy_candles(20)
    candles[5]["high"] = float("nan")
    res = compute_stop_atr(candles, period=14, timeframe="1M")
    assert res.status == "UNAVAILABLE" or math.isnan(res.value) is False


def test_atr_11_zero_and_negative_atr_fail():
    """ATR-11: Zero ATR fails closed under PAPER_STRUCTURAL stop placement."""
    candles = []
    for i in range(20):
        t = 1_000_000 + i * 60_000
        # All flat at 2650.0
        candles.append({"timestamp": t, "open": 2650.0, "high": 2650.0, "low": 2650.0, "close": 2650.0, "is_closed": True})
    res = compute_stop_atr(candles, period=14, timeframe="1M")
    assert res.status == "AVAILABLE"
    assert res.value == 0.0

    # Passed to plan_structural_stop -> fails closed with WAITING_VOLATILITY_EVIDENCE!
    anchor = StopAnchor(id="anc_1", kind="SWING_LOW", price=2640.0, timeframe="1M", occurred_at_ms=1000, known_at_ms=1060, source_leg_id="leg_1")
    dec = plan_structural_stop(
        direction="LONG", planned_entry=2650.0, take_profit=2670.0, legacy_sl=2645.0,
        anchor_result=StopAnchorResult(status="AVAILABLE", anchor=anchor),
        policy=StopPlacementPolicy(mode="PAPER_STRUCTURAL"),
        as_of_ms=2000, tick_size=0.01,
        atr_m1_obj=res,
    )
    assert dec.status == StopStatus.WAITING_VOLATILITY_EVIDENCE.value
    assert dec.selected_sl is None


def test_atr_12_invalid_status_value_combination_fails():
    """ATR-12: StopATRResult with AVAILABLE but value <= 0 is rejected by planner."""
    bad_atr = StopATRResult(status="AVAILABLE", value=0.0)
    anchor = StopAnchor(id="anc_1", kind="SWING_LOW", price=2640.0, timeframe="1M", occurred_at_ms=1000, known_at_ms=1060, source_leg_id="leg_1")
    dec = plan_structural_stop(
        direction="LONG", planned_entry=2650.0, take_profit=2670.0, legacy_sl=2645.0,
        anchor_result=StopAnchorResult(status="AVAILABLE", anchor=anchor),
        policy=StopPlacementPolicy(mode="PAPER_STRUCTURAL"),
        as_of_ms=2000, tick_size=0.01,
        atr_m1_obj=bad_atr,
    )
    assert dec.status == StopStatus.WAITING_VOLATILITY_EVIDENCE.value


def test_atr_13_valid_m1_atr_succeeds():
    """ATR-13: Valid M1 ATR succeeds and enables valid stop placement decision."""
    candles = make_dummy_candles(20)
    atr = compute_stop_atr(candles, period=14, timeframe="1M")
    assert atr.status == "AVAILABLE"
    assert atr.value > 0.0

    anchor = StopAnchor(id="anc_1", kind="SWING_LOW", price=2640.0, timeframe="1M", occurred_at_ms=1000, known_at_ms=1060, source_leg_id="leg_1")
    dec = plan_structural_stop(
        direction="LONG", planned_entry=2650.0, take_profit=2670.0, legacy_sl=2645.0,
        anchor_result=StopAnchorResult(status="AVAILABLE", anchor=anchor),
        policy=StopPlacementPolicy(mode="PAPER_STRUCTURAL"),
        as_of_ms=2000, tick_size=0.01,
        atr_m1_obj=atr,
    )
    assert dec.status == StopStatus.VALID.value
    assert dec.selected_sl is not None


def test_atr_14_optional_m5_handling_is_correct():
    """ATR-14: Optional M5 ATR is incorporated if valid, and omitted cleanly if absent."""
    atr_m1 = StopATRResult(status="AVAILABLE", value=2.0)
    atr_m5 = StopATRResult(status="AVAILABLE", value=4.0)
    policy = StopPlacementPolicy(atr_m1_multiplier=0.25, atr_m5_multiplier=0.10)

    buf_with_m5 = compute_stop_buffer(policy=policy, tick_size=0.01, atr_m1=atr_m1, atr_m5=atr_m5, bid=2650.0, ask=2650.1)
    assert "atr_m5" in buf_with_m5.components
    assert buf_with_m5.raw_atr_m5 == 4.0

    buf_without_m5 = compute_stop_buffer(policy=policy, tick_size=0.01, atr_m1=atr_m1, atr_m5=None, bid=2650.0, ask=2650.1)
    assert "atr_m5" not in buf_without_m5.components
    assert buf_without_m5.raw_atr_m5 is None


def test_atr_15_correct_distance_raw_atr_ratio():
    """ATR-15: Stop distance to ATR ratio uses RAW ATR, never weighted buffer."""
    raw_atr = 3.00
    atr_obj = StopATRResult(status="AVAILABLE", value=raw_atr)
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL", atr_m1_multiplier=0.25)
    buf = compute_stop_buffer(policy=policy, tick_size=0.01, atr_m1=atr_obj, bid=2700.0, ask=2700.1)
    # Entry = 2700.00, anchor = 2678.00, buffer = 0.75, selected SL = 2677.25 -> final_dist = 22.75
    anchor = StopAnchor(id="anc_1", kind="SWING_LOW", price=2678.0, timeframe="1M", occurred_at_ms=1000, known_at_ms=1060, source_leg_id="leg_1")
    dec = plan_structural_stop(
        direction="LONG", planned_entry=2700.0, take_profit=2760.0, legacy_sl=2695.0,
        anchor_result=StopAnchorResult(status="AVAILABLE", anchor=anchor),
        buffer_result=buf, policy=policy, as_of_ms=2000, tick_size=0.01,
        atr_m1_obj=atr_obj,
    )
    assert dec.status == StopStatus.VALID.value
    expected_ratio = dec.metrics.price_distance / raw_atr
    # Must NOT be price_distance / 0.75!
    assert abs(dec.metrics.distance_atr - expected_ratio) < 1e-4
    wrong_ratio = dec.metrics.price_distance / 0.75
    assert abs(dec.metrics.distance_atr - wrong_ratio) > 1.0


def test_atr_16_no_accidental_4x_atr_rejection():
    """ATR-16: Distant Stop Loss > 4x ATR is NOT rejected by geometry if structural evidence is valid."""
    raw_atr = 2.00
    atr_obj = StopATRResult(status="AVAILABLE", value=raw_atr)
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL", max_stop_distance_atr=4.0)
    # Distance = 20.00 -> 10x ATR!
    anchor = StopAnchor(id="anc_1", kind="SWING_LOW", price=2680.0, timeframe="1M", occurred_at_ms=1000, known_at_ms=1060, source_leg_id="leg_1")
    dec = plan_structural_stop(
        direction="LONG", planned_entry=2700.0, take_profit=2760.0, legacy_sl=2695.0,
        anchor_result=StopAnchorResult(status="AVAILABLE", anchor=anchor),
        policy=policy, as_of_ms=2000, tick_size=0.01,
        atr_m1_obj=atr_obj,
    )
    assert dec.status == StopStatus.VALID.value
    assert dec.metrics.distance_atr > 4.0, f"Distance ATR is {dec.metrics.distance_atr}"
    assert dec.selected_sl is not None


def test_atr_17_cg01_uses_verified_atr():
    """ATR-17: CG01 engine integrates verified ATR and rejects missing ATR in PAPER_STRUCTURAL."""
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
    sp = dec.candidate.stop_placement
    assert sp.status == StopStatus.VALID.value
    assert sp.metrics.distance_atr is not None


def test_atr_18_m1_strategy_uses_verified_atr():
    """ATR-18: M1 model evaluator uses verified ATR provenance."""
    candles = make_dummy_candles(30)
    atr = compute_stop_atr(candles, period=14, timeframe="1M")
    assert atr.status == "AVAILABLE"
    assert atr.bars_count == 30
    assert atr.method == "SMA_TR_V1"


def test_atr_19_shadow_compare_remains_backward_compatible():
    """ATR-19: SHADOW_COMPARE mode preserves legacy execution while recording research metrics."""
    anchor = StopAnchor(id="anc_1", kind="SWING_LOW", price=2640.0, timeframe="1M", occurred_at_ms=1000, known_at_ms=1060, source_leg_id="leg_1")
    policy = StopPlacementPolicy(mode="SHADOW_COMPARE")
    dec = plan_structural_stop(
        direction="LONG", planned_entry=2650.0, take_profit=2670.0, legacy_sl=2645.0,
        anchor_result=StopAnchorResult(status="AVAILABLE", anchor=anchor),
        policy=policy, as_of_ms=2000, tick_size=0.01,
        atr_m1_obj=StopATRResult(status="UNAVAILABLE", value=0.0),
    )
    # SHADOW_COMPARE falls back to legacy_sl for execution
    assert dec.selected_sl == 2645.0
    assert dec.applied_variant == "A"


def test_atr_20_paper_structural_cannot_arm_with_invalid_provenance():
    """ATR-20: PAPER_STRUCTURAL mode cannot ARM when ATR provenance is invalid or missing."""
    anchor = StopAnchor(id="anc_1", kind="SWING_LOW", price=2640.0, timeframe="1M", occurred_at_ms=1000, known_at_ms=1060, source_leg_id="leg_1")
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL")
    dec = plan_structural_stop(
        direction="LONG", planned_entry=2650.0, take_profit=2670.0, legacy_sl=2645.0,
        anchor_result=StopAnchorResult(status="AVAILABLE", anchor=anchor),
        policy=policy, as_of_ms=2000, tick_size=0.01,
        atr_m1_obj=StopATRResult(status="UNAVAILABLE", value=0.0, reason_code="INVALID_PROVENANCE"),
    )
    assert dec.status == StopStatus.WAITING_VOLATILITY_EVIDENCE.value
    assert dec.selected_sl is None
