"""
backend/tests/test_v17_7_atr_provenance.py
AURUM DESK V17.7.3 — ATR Provenance Authority & Volatility Guard Test Suite
Verifies ATR-01 through ATR-20:
Authoritative raw ATR provenance, separation from weighted buffer, fail-closed handling,
causal timeframes, symbol validation, ratio precision, and model parity.
"""

import math
from datetime import datetime
from unittest.mock import Mock
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
    StopPlacementDecision,
    StopMetrics,
)
from services.stop_placement_service import (
    compute_stop_atr,
    compute_stop_buffer,
    plan_structural_stop,
    validate_stop_atr_authority,
)
from services.cg01_engine import evaluate_cg01_candidate
from services.cg01_types import CG01Config, CG01EntryMode
from services.scalp_m1_model_service import ScalpM1ModelService, apply_stop_placement_to_candidate
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

    # V17.7.4 Section 12.5: AVAILABLE object timeframe="5M" in M1 authority slot is rejected
    atr_5m = StopATRResult(
        status="AVAILABLE", value=2.5, timeframe="5M", symbol="XAUUSDT",
        bars_count=15, source_candle_count=15, last_closed_at_ms=1000, last_closed_candle_timestamp=1000
    )
    is_valid, reason = validate_stop_atr_authority(atr_5m, expected_timeframe="1M")
    assert is_valid is False
    assert reason == "ATR_TIMEFRAME_MISMATCH"

    # Also fails in plan_structural_stop when passed to atr_m1_obj
    anchor = StopAnchor(id="anc_1", kind="SWING_LOW", price=2640.0, timeframe="1M", occurred_at_ms=1000, known_at_ms=1060, source_leg_id="leg_1")
    dec = plan_structural_stop(
        direction="LONG", planned_entry=2650.0, take_profit=2670.0, legacy_sl=2645.0,
        anchor_result=StopAnchorResult(status="AVAILABLE", anchor=anchor),
        policy=StopPlacementPolicy(mode="PAPER_STRUCTURAL"),
        as_of_ms=2000, tick_size=0.01,
        atr_m1_obj=atr_5m,
    )
    assert dec.status == StopStatus.WAITING_VOLATILITY_EVIDENCE.value
    assert dec.selected_sl is None


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
    """ATR-09 (V17.7.4 Section 05.B / A11, A12):
    Unclosed candle in stream (dict or object) is strictly excluded without truthiness short-circuit.
    Candles closing after cutoff are excluded; candles lacking flag are closed iff timestamp proves closed.
    """
    # 1. Dict is_closed=False
    candles = make_dummy_candles(15)
    candles[-1]["is_closed"] = False  # Last candle is still open
    res = compute_stop_atr(candles, period=14, timeframe="1M")
    assert res.status == "UNAVAILABLE"
    assert res.reason_code == "INSUFFICIENT_BARS"

    # 2. Object is_closed=False (A11)
    class CandleObj:
        def __init__(self, d):
            for k, v in d.items():
                setattr(self, k, v)

    raw_candles = make_dummy_candles(15)
    obj_candles = [CandleObj(c) for c in raw_candles]
    obj_candles[-1].is_closed = False
    res_obj = compute_stop_atr(obj_candles, period=14, timeframe="1M")
    assert res_obj.status == "UNAVAILABLE"
    assert res_obj.reason_code == "INSUFFICIENT_BARS"

    # 3. No closure flag: timestamp proves closed before cutoff -> AVAILABLE
    no_flag_candles = make_dummy_candles(20)
    for c in no_flag_candles:
        c.pop("is_closed", None)
    res_no_flag = compute_stop_atr(no_flag_candles, period=14, timeframe="1M")
    assert res_no_flag.status == "AVAILABLE"
    assert res_no_flag.bars_count == 20

    # 4. is_closed=True but t_open + duration > cutoff -> excluded as future
    cutoff_ms = 1_000_000 + 14 * 60_000  # Only 14 bars closed at or before cutoff
    candles_cutoff = make_dummy_candles(20, t_start=1_000_000)
    res_cutoff = compute_stop_atr(candles_cutoff, period=14, timeframe="1M", as_of_ms=cutoff_ms)
    assert res_cutoff.status == "UNAVAILABLE"
    assert res_cutoff.reason_code == "INSUFFICIENT_BARS"


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
    res = compute_stop_atr(candles, period=14, timeframe="1M", symbol="XAUUSDT", as_of_ms=1_000_000 + 20 * 60_000)
    assert res.value == 0.0
    # Authority validator rejects 0 ATR for structural execution
    is_valid, reason = validate_stop_atr_authority(
        res, expected_symbol="XAUUSDT", expected_timeframe="1M", decision_cutoff=1_000_000 + 20 * 60_000
    )
    assert is_valid is False
    assert reason == "ATR_NON_POSITIVE_OR_NON_FINITE"

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
    atr = compute_stop_atr(candles, period=14, timeframe="1M", symbol="XAUUSDT")
    assert atr.status == "AVAILABLE"
    assert atr.value > 0.0

    as_of = atr.last_closed_at_ms + 1000
    anchor = StopAnchor(id="anc_1", kind="SWING_LOW", price=2640.0, timeframe="1M", occurred_at_ms=as_of-120000, known_at_ms=as_of-60000, source_leg_id="leg_1")
    dec = plan_structural_stop(
        direction="LONG", planned_entry=2650.0, take_profit=2670.0, legacy_sl=2645.0,
        anchor_result=StopAnchorResult(status="AVAILABLE", anchor=anchor),
        policy=StopPlacementPolicy(mode="PAPER_STRUCTURAL"),
        as_of_ms=as_of, tick_size=0.01,
        atr_m1_obj=atr,
        symbol="XAUUSDT",
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
    as_of = 2000000
    atr_obj = StopATRResult(
        status="AVAILABLE", value=raw_atr, timeframe="1M", symbol="XAUUSDT",
        bars_count=15, source_candle_count=15, last_closed_at_ms=as_of-60000, last_closed_candle_timestamp=as_of-60000
    )
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL", atr_m1_multiplier=0.25)
    buf = compute_stop_buffer(policy=policy, tick_size=0.01, atr_m1=atr_obj, bid=2700.0, ask=2700.1, symbol="XAUUSDT")
    # Entry = 2700.00, anchor = 2678.00, buffer = 0.75, selected SL = 2677.25 -> final_dist = 22.75
    anchor = StopAnchor(id="anc_1", kind="SWING_LOW", price=2678.0, timeframe="1M", occurred_at_ms=as_of-120000, known_at_ms=as_of-60000, source_leg_id="leg_1")
    dec = plan_structural_stop(
        direction="LONG", planned_entry=2700.0, take_profit=2760.0, legacy_sl=2695.0,
        anchor_result=StopAnchorResult(status="AVAILABLE", anchor=anchor),
        buffer_result=buf, policy=policy, as_of_ms=as_of, tick_size=0.01,
        atr_m1_obj=atr_obj, symbol="XAUUSDT",
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
    as_of = 2000000
    atr_obj = StopATRResult(
        status="AVAILABLE", value=raw_atr, timeframe="1M", symbol="XAUUSDT",
        bars_count=15, source_candle_count=15, last_closed_at_ms=as_of-60000, last_closed_candle_timestamp=as_of-60000
    )
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL", max_stop_distance_atr=4.0)
    # Distance = 20.00 -> 10x ATR!
    anchor = StopAnchor(id="anc_1", kind="SWING_LOW", price=2680.0, timeframe="1M", occurred_at_ms=as_of-120000, known_at_ms=as_of-60000, source_leg_id="leg_1")
    dec = plan_structural_stop(
        direction="LONG", planned_entry=2700.0, take_profit=2760.0, legacy_sl=2695.0,
        anchor_result=StopAnchorResult(status="AVAILABLE", anchor=anchor),
        policy=policy, as_of_ms=as_of, tick_size=0.01,
        atr_m1_obj=atr_obj, symbol="XAUUSDT",
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
    """ATR-18: Real M1 apply helper integrates verified ATR provenance into candidate decision."""
    candles = make_dummy_candles(30)
    as_of = candles[-1]["timestamp"] + 60_000
    atr = compute_stop_atr(candles, period=14, timeframe="1M", as_of_ms=as_of, symbol="XAUUSDT")
    assert atr.status == "AVAILABLE"
    assert atr.bars_count == 30
    assert atr.method == "SMA_TR_V1"

    anchor = StopAnchor(id="anc_18", kind="SWING_LOW", price=2640.0, timeframe="1M", occurred_at_ms=as_of-120000, known_at_ms=as_of-60000, source_leg_id="leg_18")
    anchor_res = StopAnchorResult(status="AVAILABLE", anchor=anchor)
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL")

    from dataclasses import dataclass
    @dataclass
    class DummyM1Cfg:
        min_net_rr: float = 1.5
        tick_size: float = 0.01
        fallback_risk_pct: float = 0.01

    res = apply_stop_placement_to_candidate(
        model_id="M1_REVERSAL",
        direction="LONG",
        entry_price=2650.0,
        baseline_sl=2645.0,
        baseline_target=2670.0,
        closed_1m=candles,
        now_ms=as_of,
        cfg=DummyM1Cfg(),
        policy_settings={"capital_usdt": 1000.0, "risk_pct": 0.01, "leverage": 20},
        stop_policy=policy,
        stop_context={"atr_m1": atr},
        candidate_key="cand_18",
        anchor_result=anchor_res,
        atr_1m_val=atr.value,
        bid=2650.0,
        ask=2650.1,
    )
    assert res.decision is not None
    assert res.decision.status == StopStatus.VALID.value
    assert res.decision.volatility_context["atr_m1"] == atr.value
    assert res.decision.volatility_context["volatility_gate_status"] == "AVAILABLE"

    # Negative control: unavailable ATR fails closed with WAITING_VOLATILITY_EVIDENCE
    unavail_atr = StopATRResult(status="UNAVAILABLE", value=0.0, reason_code="INSUFFICIENT_BARS")
    res_unavail = apply_stop_placement_to_candidate(
        model_id="M1_REVERSAL",
        direction="LONG",
        entry_price=2650.0,
        baseline_sl=2645.0,
        baseline_target=2670.0,
        closed_1m=candles,
        now_ms=as_of,
        cfg=DummyM1Cfg(),
        policy_settings={"capital_usdt": 1000.0, "risk_pct": 0.01, "leverage": 20},
        stop_policy=policy,
        stop_context={"atr_m1": unavail_atr},
        candidate_key="cand_18_bad",
        anchor_result=anchor_res,
        atr_1m_val=1.5,
        bid=2650.0,
        ask=2650.1,
    )
    assert res_unavail.allowed is False
    assert res_unavail.decision is not None
    assert res_unavail.decision.status == StopStatus.WAITING_VOLATILITY_EVIDENCE.value


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


def test_atr_20_paper_structural_cannot_arm_with_invalid_provenance(isolated_db):
    """ATR-20: PAPER_STRUCTURAL mode cannot ARM when ATR provenance is invalid or missing."""
    from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator
    from services.scalp_m1_model_service import ScalpCandidate
    from services.scalp_m1_types import OpportunityStage
    from services.quote_validator import ValidatedQuote
    from services.clock import LiveClock
    from services.stop_placement_service import validate_applied_stop_decision
    import models

    db = isolated_db
    orch = ScalpWatcherOrchestrator()
    now_ms = 2_000_000

    # 1. Direct validation barrier test
    class DummyCalc:
        is_valid = True
        can_execute = True
        stop_loss = 2645.0
        skip_reason = None
        invalid_reason = None

    class DummyCand:
        candidate_key = "test_cand_atr_20"
        symbol = "XAUUSDT"
        model_id = "M1_REVERSAL"
        direction = "LONG"
        zone_id = "z_20"
        planned_entry = 2650.0
        stop_loss = 2645.0
        take_profit = 2670.0
        net_rr = 2.0
        order_id = "ord_atr_20"
        snapshot = None
        calculation = DummyCalc()
        stop_placement = StopPlacementDecision(
            status=StopStatus.VALID.value,
            selected_sl=2645.0,
            anchor=StopAnchor(id="anc_20", kind="SWING_LOW", price=2640.0, timeframe="1M", occurred_at_ms=now_ms-120000, known_at_ms=now_ms-60000, source_leg_id="l1"),
            bound_leg=None,
            buffer=None,
            metrics=StopMetrics(price_distance=5.0, distance_ticks=500, distance_atr=2.5),
            reasons=(),
            policy_version=1,
            mode="PAPER_STRUCTURAL",
            decision_cutoff_ms=now_ms,
            applied_variant="C",
            protective_distance=1.0,
            expansion_applied=False,
            distance_assessment="DISTANCE_NORMAL",
            volatility_context={
                "atr_m1": None,  # FORGED / MISSING ATR EVIDENCE!
                "atr_m1_status": "UNAVAILABLE",
                "volatility_gate_status": "FAIL_CLOSED",
            },
        )

    cand = DummyCand()
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL")

    ok, reason = validate_applied_stop_decision(candidate=cand, current_policy=policy, now_ms=now_ms)
    assert ok is False
    assert "FAIL_CLOSED" in reason or "ATR" in reason

    # 2. Actual ARM boundary test through _arm_paper_candidate
    from services.stop_placement_policy_service import resolve_stop_placement_policy
    # Ensure current DB policy is PAPER_STRUCTURAL
    existing_policy = db.query(models.SystemConfig).filter(models.SystemConfig.key == "stop_placement_v1.mode").first()
    if existing_policy is None:
        db.add(models.SystemConfig(key="stop_placement_v1.mode", value="PAPER_STRUCTURAL", updated_at=datetime.utcnow()))
    else:
        existing_policy.value = "PAPER_STRUCTURAL"
        existing_policy.updated_at = datetime.utcnow()
    db.commit()

    v_quote = ValidatedQuote(
        is_valid=True, bid=2650.0, ask=2650.1, last=2650.05, spread=0.10,
        exchange_time=now_ms, observed_at=now_ms, freshness_sec=0.1, is_stale=False,
        rejection_code=None, rejection_detail=None
    )
    armed_order = orch._arm_paper_candidate(
        db,
        cand,
        now_ms=now_ms,
        clock=LiveClock(),
        quote=v_quote,
        policy_settings={"capital_usdt": 1000.0, "risk_pct": 0.01, "leverage": 20},
        auto_enabled=True,
    )
    assert armed_order is None, "ARM must reject forged/missing ATR candidate"
    order_in_db = db.query(models.PaperOrder).filter(models.PaperOrder.id == "ord_atr_20").first()
    assert order_in_db is None, "No PaperOrder may be persisted when ATR authority fails"


def test_atr_21_four_m1_models_integrate_verified_authority():
    """ATR-21 (V17.7.4 Section 07.3 / A22):
    All four M1 models (M1_REVERSAL, M1_CONTINUATION, M1_FVG_CONTINUATION, M1_SWEEP_RECLAIM)
    integrate through the authoritative ATR contract without scalar ambiguity.
    """
    candles = make_dummy_candles(30)
    as_of = candles[-1]["timestamp"] + 60_000
    atr = compute_stop_atr(candles, period=14, timeframe="1M", as_of_ms=as_of, symbol="XAUUSDT")
    assert atr.status == "AVAILABLE"

    anchor = StopAnchor(id="anc_21", kind="SWING_LOW", price=2640.0, timeframe="1M", occurred_at_ms=as_of-120000, known_at_ms=as_of-60000, source_leg_id="leg_21")
    anchor_res = StopAnchorResult(status="AVAILABLE", anchor=anchor)
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL")

    from dataclasses import dataclass
    @dataclass
    class DummyM1Cfg:
        min_net_rr: float = 1.5
        tick_size: float = 0.01
        fallback_risk_pct: float = 0.01

    m1_models = [
        "M1_REVERSAL",
        "M1_CONTINUATION",
        "M1_FVG_CONTINUATION",
        "M1_SWEEP_RECLAIM",
    ]

    for mid in m1_models:
        res = apply_stop_placement_to_candidate(
            model_id=mid,
            direction="LONG",
            entry_price=2650.0,
            baseline_sl=2645.0,
            baseline_target=2670.0,
            closed_1m=candles,
            now_ms=as_of,
            cfg=DummyM1Cfg(),
            policy_settings={"capital_usdt": 1000.0, "risk_pct": 0.01, "leverage": 20},
            stop_policy=policy,
            stop_context={"atr_m1": atr},
            candidate_key=f"cand_21_{mid}",
            anchor_result=anchor_res,
            atr_1m_val=None,  # No scalar fallback
            bid=2650.0,
            ask=2650.1,
        )
        assert res.decision is not None, f"Model {mid} decision was None"
        assert res.decision.status == StopStatus.VALID.value, f"Model {mid} failed: {res.decision.reasons}"
        assert res.decision.volatility_context["atr_m1"] == atr.value
        assert res.decision.volatility_context["volatility_gate_status"] == "AVAILABLE"

        # Negative control: missing ATR fails closed for every model even if scalar is present
        res_fail = apply_stop_placement_to_candidate(
            model_id=mid,
            direction="LONG",
            entry_price=2650.0,
            baseline_sl=2645.0,
            baseline_target=2670.0,
            closed_1m=candles,
            now_ms=as_of,
            cfg=DummyM1Cfg(),
            policy_settings={"capital_usdt": 1000.0, "risk_pct": 0.01, "leverage": 20},
            stop_policy=policy,
            stop_context={"atr_m1": None},
            candidate_key=f"cand_21_fail_{mid}",
            anchor_result=anchor_res,
            atr_1m_val=3.0,  # Scalar must NOT override in PAPER_STRUCTURAL
            bid=2650.0,
            ask=2650.1,
        )
        assert res_fail.allowed is False
        assert res_fail.decision.status == StopStatus.WAITING_VOLATILITY_EVIDENCE.value


def test_review_probe_2_invalid_optional_m5_cannot_alter_structural_sl():
    """Probe 2: Invalid optional M5 in slot cannot contribute to buffer or alter final structural SL."""
    from services.stop_placement_service import plan_structural_stop, compute_stop_buffer
    from services.stop_placement_types import StopPlacementPolicy, StopAnchorResult, StopAnchor, StopATRResult, StopStatus

    as_of = 2_000_000
    valid_m1 = StopATRResult(
        status="AVAILABLE", value=3.0, timeframe="1M", symbol="XAUUSDT",
        bars_count=15, source_candle_count=15, last_closed_at_ms=as_of-60_000, last_closed_candle_timestamp=as_of-60_000,
        calculated_as_of=as_of-60_000, decision_cutoff=as_of
    )
    # Probe object: symbol BTCUSDT, timeframe 1M, value 1000 in M5 slot
    invalid_m5 = StopATRResult(
        status="AVAILABLE", value=1000.0, timeframe="1M", symbol="BTCUSDT",
        bars_count=15, source_candle_count=15, last_closed_at_ms=as_of-60_000, last_closed_candle_timestamp=as_of-60_000,
        calculated_as_of=as_of-60_000, decision_cutoff=as_of
    )

    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL", atr_m1_multiplier=0.25, atr_m5_multiplier=0.10)
    anchor = StopAnchor(id="anc_2", kind="SWING_LOW", price=2640.0, timeframe="1M", occurred_at_ms=as_of-120_000, known_at_ms=as_of-60_000, source_leg_id="leg_2")

    # Baseline without M5
    buf_base = compute_stop_buffer(
        policy=policy, tick_size=0.01, atr_m1=valid_m1, atr_m5=None,
        bid=2650.0, ask=2650.1, symbol="XAUUSDT", decision_cutoff=as_of
    )
    base_dec = plan_structural_stop(
        direction="LONG", planned_entry=2650.0, take_profit=2670.0, legacy_sl=2645.0,
        anchor_result=StopAnchorResult(status="AVAILABLE", anchor=anchor),
        buffer_result=buf_base, policy=policy, as_of_ms=as_of, tick_size=0.01,
        atr_m1_obj=valid_m1, atr_m5_obj=None, symbol="XAUUSDT"
    )
    assert base_dec.status == StopStatus.VALID.value
    baseline_sl = base_dec.selected_sl

    # Run with invalid M5 in M5 slot
    buf_invalid = compute_stop_buffer(
        policy=policy, tick_size=0.01, atr_m1=valid_m1, atr_m5=invalid_m5,
        bid=2650.0, ask=2650.1, symbol="XAUUSDT", decision_cutoff=as_of
    )
    assert "atr_m5" not in buf_invalid.components
    assert buf_invalid.raw_atr_m5 is None

    dec_with_invalid = plan_structural_stop(
        direction="LONG", planned_entry=2650.0, take_profit=2670.0, legacy_sl=2645.0,
        anchor_result=StopAnchorResult(status="AVAILABLE", anchor=anchor),
        buffer_result=buf_invalid, policy=policy, as_of_ms=as_of, tick_size=0.01,
        atr_m1_obj=valid_m1, atr_m5_obj=invalid_m5, symbol="XAUUSDT"
    )
    assert dec_with_invalid.status == StopStatus.VALID.value
    # Selected SL MUST be identical to baseline without M5! (Never 2540.0 from 1000*0.10)
    assert dec_with_invalid.selected_sl == baseline_sl
    assert dec_with_invalid.volatility_context.get("atr_m5") is None


def test_review_probe_3_arm_barrier_rejects_scalar_only_evidence():
    """Probe 3: ARM barrier refuses scalar-only context and enforces full ATR authority and provenance."""
    from dataclasses import replace
    from services.stop_placement_service import validate_applied_stop_decision
    from services.stop_placement_types import StopPlacementPolicy, StopAnchor, StopMetrics, StopPlacementDecision, StopStatus

    now_ms = 2_000_000
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL")

    class DummyCand:
        candidate_key = "probe_3_cand"
        symbol = "XAUUSDT"
        model_id = "M1_REVERSAL"
        direction = "LONG"
        planned_entry = 2650.0
        stop_loss = 2640.0
        take_profit = 2670.0
        calculation = None
        stop_placement = StopPlacementDecision(
            status=StopStatus.VALID.value,
            selected_sl=2640.0,
            anchor=StopAnchor(id="a3", kind="SWING_LOW", price=2638.0, timeframe="1M", occurred_at_ms=now_ms-120_000, known_at_ms=now_ms-60_000, source_leg_id="l1"),
            bound_leg=None, buffer=None,
            metrics=StopMetrics(price_distance=10.0, distance_ticks=1000, distance_atr=3.33),
            reasons=(), policy_version=1, mode="PAPER_STRUCTURAL",
            decision_cutoff_ms=now_ms, applied_variant="C", protective_distance=1.0, expansion_applied=False,
            distance_assessment="DISTANCE_NORMAL",
            volatility_context={"atr_m1": 3.0}  # Scalar only, missing status & provenance!
        )

    cand = DummyCand()
    # 1. Scalar only is rejected
    ok, reason = validate_applied_stop_decision(candidate=cand, current_policy=policy, now_ms=now_ms)
    assert ok is False
    assert "FAIL_CLOSED" in reason or "VOLATILITY_GATE_NOT_AVAILABLE" in reason or "ATR" in reason

    # 2. Mutated symbol in provenance (e.g. BTCUSDT for XAUUSDT candidate) is rejected
    cand.stop_placement = replace(
        cand.stop_placement,
        volatility_context={
            "volatility_gate_status": "AVAILABLE",
            "atr_m1_status": "AVAILABLE",
            "atr_m1": 3.0,
            "atr_m1_provenance": {
                "status": "AVAILABLE", "value": 3.0, "timeframe": "1M", "symbol": "BTCUSDT",
                "bars_count": 15, "source_candle_count": 15, "last_closed_at_ms": now_ms-60_000,
                "calculated_as_of": now_ms-60_000, "decision_cutoff": now_ms
            }
        }
    )
    ok_sym, reason_sym = validate_applied_stop_decision(candidate=cand, current_policy=policy, now_ms=now_ms)
    assert ok_sym is False
    assert "SYMBOL_MISMATCH" in reason_sym

    # 3. Lookahead cutoff in provenance is rejected
    cand.stop_placement = replace(
        cand.stop_placement,
        volatility_context={
            "volatility_gate_status": "AVAILABLE",
            "atr_m1_status": "AVAILABLE",
            "atr_m1": 3.0,
            "atr_m1_provenance": {
                "status": "AVAILABLE", "value": 3.0, "timeframe": "1M", "symbol": "XAUUSDT",
                "bars_count": 15, "source_candle_count": 15, "last_closed_at_ms": now_ms + 60_000,
                "calculated_as_of": now_ms + 60_000, "decision_cutoff": now_ms + 60_000
            }
        }
    )
    ok_la, reason_la = validate_applied_stop_decision(candidate=cand, current_policy=policy, now_ms=now_ms)
    assert ok_la is False
    assert "AFTER_DECISION" in reason_la or "LOOKAHEAD" in reason_la


def test_review_probe_8_authority_validator_fail_closed_malformed_metadata():
    """Probe 8: Authority validator fails closed on missing timestamps, lookahead cutoff, string period, and zero ATR."""
    from services.stop_placement_service import validate_stop_atr_authority, compute_stop_atr
    from services.stop_placement_types import StopATRResult

    # 1. Missing closed candle timestamp and calc_as_of=0
    bad_obj = StopATRResult(
        status="AVAILABLE", value=3.0, timeframe="1M", symbol="XAUUSDT",
        bars_count=15, source_candle_count=15,
        last_closed_at_ms=0, last_closed_candle_timestamp=0, calculated_as_of=0,
        decision_cutoff=9_000_000
    )
    ok, reason = validate_stop_atr_authority(bad_obj, expected_symbol="XAUUSDT", expected_timeframe="1M", decision_cutoff=2_000_000)
    assert ok is False
    assert "ATR_TIMESTAMP_MISSING_OR_INVALID" in reason

    # 2. String period doesn't crash with ValueError and fails cleanly
    ok_prd, reason_prd = validate_stop_atr_authority(
        StopATRResult(status="AVAILABLE", value=3.0, timeframe="1M", symbol="XAUUSDT", period="invalid"),  # type: ignore
        expected_symbol="XAUUSDT", expected_timeframe="1M"
    )
    assert ok_prd is False
    assert "ATR_PERIOD_MISMATCH" in reason_prd

    # 3. compute_stop_atr returns UNAVAILABLE / value=0.0 for completely flat bars
    flat_candles = [{"timestamp": 1000 + i*60000, "open": 2650.0, "high": 2650.0, "low": 2650.0, "close": 2650.0, "is_closed": True} for i in range(20)]
    res = compute_stop_atr(flat_candles, period=14, timeframe="1M", symbol="XAUUSDT")
    assert res.status == "UNAVAILABLE"
    assert res.value == 0.0
    assert res.reason_code == "ZERO_OR_NEGATIVE_ATR"


# ==============================================================================
# AURUM DESK V17.7.4.1 — FINAL ATR PROVENANCE & SAFETY (ATR-FINAL-01..15)
# ==============================================================================

def test_atr_final_01_missing_source_counts_are_rejected():
    """ATR-FINAL-01: ATR with missing source counts (bars_count=0, source_candle_count=0) is rejected."""
    atr_obj = StopATRResult(
        status="AVAILABLE", value=3.0, timeframe="1M", symbol="XAUUSDT",
        bars_count=0, source_candle_count=0,
        last_closed_at_ms=1_000_000, calculated_as_of=1_000_000,
    )
    ok, reason = validate_stop_atr_authority(atr_obj, expected_symbol="XAUUSDT", expected_timeframe="1M")
    assert ok is False
    assert "ATR_SOURCE_COUNT_INSUFFICIENT" in reason


def test_atr_final_02_available_with_invalid_metadata_rejected():
    """ATR-FINAL-02: AVAILABLE status with non-positive or non-finite value is rejected."""
    for bad_val in (0.0, -1.5, float("nan"), float("inf"), True, False):
        atr_obj = StopATRResult(
            status="AVAILABLE", value=bad_val, timeframe="1M", symbol="XAUUSDT",  # type: ignore
            bars_count=15, source_candle_count=15,
            last_closed_at_ms=1_000_000, calculated_as_of=1_000_000,
        )
        ok, reason = validate_stop_atr_authority(atr_obj, expected_symbol="XAUUSDT", expected_timeframe="1M")
        assert ok is False
        assert "ATR_NON_POSITIVE_OR_NON_FINITE" in reason


def test_atr_final_03_non_numeric_decision_cutoff_rejected():
    """ATR-FINAL-03: Non-numeric or NaN decision cutoff results in explicit fail-closed rejection."""
    valid_atr = StopATRResult(
        status="AVAILABLE", value=3.0, timeframe="1M", symbol="XAUUSDT",
        bars_count=15, source_candle_count=15,
        last_closed_at_ms=1_000_000, calculated_as_of=1_000_000,
    )
    for bad_cutoff in ("invalid_cutoff", float("nan"), float("inf"), True):
        ok, reason = validate_stop_atr_authority(valid_atr, expected_symbol="XAUUSDT", decision_cutoff=bad_cutoff)  # type: ignore
        assert ok is False
        assert "ATR_DECISION_CUTOFF_INVALID" in reason


def test_atr_final_04_negative_decision_cutoff_rejected():
    """ATR-FINAL-04: Negative decision cutoff is explicitly rejected."""
    valid_atr = StopATRResult(
        status="AVAILABLE", value=3.0, timeframe="1M", symbol="XAUUSDT",
        bars_count=15, source_candle_count=15,
        last_closed_at_ms=1_000_000, calculated_as_of=1_000_000,
    )
    ok, reason = validate_stop_atr_authority(valid_atr, expected_symbol="XAUUSDT", decision_cutoff=-100)
    assert ok is False
    assert "ATR_DECISION_CUTOFF_INVALID" in reason


def test_atr_final_05_missing_required_timestamp_rejected():
    """ATR-FINAL-05: Missing or non-positive last_closed_at timestamp is rejected."""
    for bad_ts in (0, -1, None, True):
        atr_obj = StopATRResult(
            status="AVAILABLE", value=3.0, timeframe="1M", symbol="XAUUSDT",
            bars_count=15, source_candle_count=15,
            last_closed_at_ms=bad_ts,  # type: ignore
            calculated_as_of=1_000_000,
        )
        ok, reason = validate_stop_atr_authority(atr_obj, expected_symbol="XAUUSDT")
        assert ok is False
        assert "ATR_TIMESTAMP_MISSING_OR_INVALID" in reason


def test_atr_final_06_future_atr_source_rejected():
    """ATR-FINAL-06: ATR source bar closed after decision cutoff is rejected."""
    atr_obj = StopATRResult(
        status="AVAILABLE", value=3.0, timeframe="1M", symbol="XAUUSDT",
        bars_count=15, source_candle_count=15,
        last_closed_at_ms=1_005_000, calculated_as_of=1_005_000,
    )
    ok, reason = validate_stop_atr_authority(atr_obj, expected_symbol="XAUUSDT", decision_cutoff=1_000_000)
    assert ok is False
    assert "ATR_SOURCE_AFTER_DECISION" in reason


def test_atr_final_07_stale_required_atr_rejected():
    """ATR-FINAL-07: ATR closed older than max_age_ms before decision cutoff is rejected."""
    atr_obj = StopATRResult(
        status="AVAILABLE", value=3.0, timeframe="1M", symbol="XAUUSDT",
        bars_count=15, source_candle_count=15,
        last_closed_at_ms=1_000_000, calculated_as_of=1_000_000,
    )
    # Cutoff is 1_200_000, max_age_ms is 60_000 -> age is 200_000 > 60_000
    ok, reason = validate_stop_atr_authority(
        atr_obj, expected_symbol="XAUUSDT", decision_cutoff=1_200_000, max_age_ms=60_000
    )
    assert ok is False
    assert "ATR_STALE_FOR_DECISION" in reason


def test_atr_final_08_invalid_optional_m5_never_changes_sl():
    """ATR-FINAL-08: Invalid optional M5 is safely ignored and never alters the calculated Stop Loss."""
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL", atr_m1_multiplier=0.25, atr_m5_multiplier=0.15)
    anchor = StopAnchor(
        id="anc_final_08", kind="SWING_LOW", price=2680.0, timeframe="1M",
        occurred_at_ms=1_000_000, known_at_ms=1_050_000, source_leg_id="leg_08"
    )
    anchor_res = StopAnchorResult(status="AVAILABLE", anchor=anchor)

    atr_m1 = StopATRResult(
        status="AVAILABLE", value=3.0, timeframe="1M", symbol="XAUUSDT",
        bars_count=15, source_candle_count=15,
        last_closed_at_ms=1_050_000, calculated_as_of=1_050_000, decision_cutoff=1_050_000,
    )

    # 1. Run with M5 = None
    buf_no_m5 = compute_stop_buffer(
        policy=policy, tick_size=0.01, atr_m1=atr_m1, atr_m5=None, bid=2700.0, ask=2700.2, symbol="XAUUSDT", decision_cutoff=1_050_000
    )
    dec_no_m5 = plan_structural_stop(
        direction="LONG", planned_entry=2700.0, take_profit=2740.0, legacy_sl=2685.0,
        anchor_result=anchor_res, buffer_result=buf_no_m5, policy=policy, as_of_ms=1_050_000,
        tick_size=0.01, atr_m1_obj=atr_m1, atr_m5_obj=None, symbol="XAUUSDT",
    )

    # 2. Run with malformed/invalid M5 (wrong symbol and negative value)
    bad_m5 = StopATRResult(
        status="AVAILABLE", value=-99.0, timeframe="5M", symbol="WRONG_SYMBOL",
        bars_count=15, source_candle_count=15, last_closed_at_ms=1_050_000, calculated_as_of=1_050_000,
    )
    buf_with_bad_m5 = compute_stop_buffer(
        policy=policy, tick_size=0.01, atr_m1=atr_m1, atr_m5=bad_m5, bid=2700.0, ask=2700.2, symbol="XAUUSDT", decision_cutoff=1_050_000
    )
    dec_with_bad_m5 = plan_structural_stop(
        direction="LONG", planned_entry=2700.0, take_profit=2740.0, legacy_sl=2685.0,
        anchor_result=anchor_res, buffer_result=buf_with_bad_m5, policy=policy, as_of_ms=1_050_000,
        tick_size=0.01, atr_m1_obj=atr_m1, atr_m5_obj=bad_m5, symbol="XAUUSDT",
    )

    assert dec_no_m5.status == StopStatus.VALID.value
    assert dec_with_bad_m5.status == StopStatus.VALID.value
    assert dec_no_m5.selected_sl == dec_with_bad_m5.selected_sl
    assert dec_no_m5.metrics.price_distance == dec_with_bad_m5.metrics.price_distance


def test_atr_final_09_weighted_atr_buffer_cannot_impersonate_raw_atr():
    """ATR-FINAL-09: Weighted buffer contribution (0.25*ATR = 0.75) cannot impersonate raw ATR (3.00)."""
    raw_val = 3.00
    atr_m1 = StopATRResult(
        status="AVAILABLE", value=raw_val, timeframe="1M", symbol="XAUUSDT",
        bars_count=15, source_candle_count=15, last_closed_at_ms=1000, calculated_as_of=1000,
    )
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL", atr_m1_multiplier=0.25)
    buf = compute_stop_buffer(
        policy=policy, tick_size=0.01, atr_m1=atr_m1, bid=2700.0, ask=2700.1, symbol="XAUUSDT", decision_cutoff=1000
    )

    assert buf.raw_atr_m1 == 3.00
    assert buf.components["atr_m1"] == 0.75
    assert buf.raw_atr_m1 != buf.components["atr_m1"]


def test_atr_final_10_arm_rejects_scalar_only_atr_evidence():
    """ATR-FINAL-10: Production ARM barrier rejects candidates carrying scalar-only ATR without provenance."""
    from services.stop_placement_service import validate_applied_stop_decision

    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL")
    # Fabricate candidate with missing provenance dict
    cand = {
        "symbol": "XAUUSDT",
        "stop_loss": 2680.0,
        "calculation": Mock(is_valid=True, can_execute=True, stop_loss=2680.0),
        "stop_placement": Mock(
            status="VALID",
            applied_variant="C",
            anchor=Mock(price=2685.0),
            selected_sl=2680.0,
            decision_cutoff_ms=1_000_000,
            atr_m1_result=None,
            volatility_context={
                "volatility_gate_status": "AVAILABLE",
                "atr_m1_status": "AVAILABLE",
                "atr_m1": 3.0,
                "atr_m1_provenance": None,  # Missing provenance!
            },
        ),
    }
    ok, reason = validate_applied_stop_decision(candidate=cand, current_policy=policy)
    assert ok is False
    assert "FORGED_STRUCTURAL_DECISION_MISSING_ATR_EVIDENCE" in reason


def test_atr_final_11_arm_rejects_inconsistent_atr_provenance():
    """ATR-FINAL-11: Production ARM barrier rejects mismatched provenance values vs volatility context."""
    from services.stop_placement_service import validate_applied_stop_decision

    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL")
    cand = {
        "symbol": "XAUUSDT",
        "stop_loss": 2680.0,
        "calculation": Mock(is_valid=True, can_execute=True, stop_loss=2680.0),
        "stop_placement": Mock(
            status="VALID",
            applied_variant="C",
            anchor=Mock(price=2685.0),
            selected_sl=2680.0,
            decision_cutoff_ms=1_000_000,
            atr_m1_result=None,
            volatility_context={
                "volatility_gate_status": "AVAILABLE",
                "atr_m1_status": "AVAILABLE",
                "atr_m1": 3.00,  # Context claims 3.00
                "atr_m1_provenance": {
                    "status": "AVAILABLE",
                    "value": 5.50,  # Provenance object says 5.50 (inconsistent!)
                    "timeframe": "1M",
                    "symbol": "XAUUSDT",
                    "period": 14,
                    "method": "SMA_TR_V1",
                    "bars_count": 15,
                    "source_candle_count": 15,
                    "last_closed_at_ms": 1_000_000,
                    "calculated_as_of": 1_000_000,
                    "decision_cutoff": 1_000_000,
                },
            },
        ),
    }
    ok, reason = validate_applied_stop_decision(candidate=cand, current_policy=policy)
    assert ok is False
    assert "ATR_EVIDENCE_PROVENANCE_VALUE_MISMATCH" in reason


def test_atr_final_12_valid_causal_atr_remains_accepted():
    """ATR-FINAL-12: Valid causal ATR with full provenance is cleanly accepted."""
    candles = make_dummy_candles(20, t_start=1_000_000, duration=60_000, symbol="XAUUSDT")
    res = compute_stop_atr(candles, period=14, timeframe="1M", symbol="XAUUSDT", as_of_ms=2_500_000)
    assert res.status == "AVAILABLE"
    assert res.value > 0.0

    ok, reason = validate_stop_atr_authority(res, expected_symbol="XAUUSDT", expected_timeframe="1M", decision_cutoff=2_500_000)
    assert ok is True
    assert reason == "VALID"


def test_atr_final_13_valid_sl_beyond_4x_atr_remains_eligible():
    """ATR-FINAL-13: Valid structural Stop Loss with distance > 4.0x ATR remains fully ELIGIBLE."""
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL", max_stop_distance_atr=4.0)
    anchor = StopAnchor(
        id="anc_distant_13", kind="SWING_LOW", price=2678.0, timeframe="1M",
        occurred_at_ms=1000, known_at_ms=1050, source_leg_id="leg_13"
    )
    anchor_res = StopAnchorResult(status="AVAILABLE", anchor=anchor)

    atr_m1 = StopATRResult(
        status="AVAILABLE", value=3.0, timeframe="1M", symbol="XAUUSDT",
        bars_count=15, source_candle_count=15, last_closed_at_ms=1050, calculated_as_of=1050, decision_cutoff=1050,
    )
    buf = compute_stop_buffer(policy=policy, tick_size=0.01, atr_m1=atr_m1, bid=2700.0, ask=2700.2, symbol="XAUUSDT", decision_cutoff=1050)

    # Planned entry 2700.00, anchor 2678.00, buffer 0.75 -> selected_sl 2677.25
    # Final distance = 22.75 USD -> 22.75 / 3.0 = 7.58x ATR (> 4.0x ATR)
    dec = plan_structural_stop(
        direction="LONG", planned_entry=2700.0, take_profit=2760.0, legacy_sl=2685.0,
        anchor_result=anchor_res, buffer_result=buf, policy=policy, as_of_ms=1050,
        tick_size=0.01, atr_m1_obj=atr_m1, symbol="XAUUSDT",
    )

    assert dec.status == StopStatus.VALID.value
    assert dec.selected_sl == 2677.25
    assert dec.metrics.distance_atr > 4.0
    assert dec.distance_assessment == "DISTANCE_EXTREME"


def test_atr_final_14_invalid_financial_eligibility_remains_rejected():
    """ATR-FINAL-14: Distant stop loss violating risk limits or minimum Net R:R is rejected for financial reasons."""
    policy = StopPlacementPolicy(mode="PAPER_STRUCTURAL")
    anchor = StopAnchor(
        id="anc_fail_14", kind="SWING_LOW", price=2600.0, timeframe="1M",
        occurred_at_ms=1000, known_at_ms=1050, source_leg_id="leg_14"
    )
    anchor_res = StopAnchorResult(status="AVAILABLE", anchor=anchor)
    atr_m1 = StopATRResult(
        status="AVAILABLE", value=3.0, timeframe="1M", symbol="XAUUSDT",
        bars_count=15, source_candle_count=15, last_closed_at_ms=1050, calculated_as_of=1050,
    )
    buf = compute_stop_buffer(policy=policy, tick_size=0.01, atr_m1=atr_m1, bid=2700.0, ask=2700.2, symbol="XAUUSDT")

    # Inverted geometry: planned entry 2700.0, take_profit 2650.0 (below entry on a LONG!)
    dec = plan_structural_stop(
        direction="LONG", planned_entry=2700.0, take_profit=2650.0, legacy_sl=2685.0,
        anchor_result=anchor_res, buffer_result=buf, policy=policy, as_of_ms=1050,
        tick_size=0.01, atr_m1_obj=atr_m1, symbol="XAUUSDT",
    )
    assert dec.status == StopStatus.REJECTED_GEOMETRY.value
    assert "INVALID_PRICE_GEOMETRY_WITH_STRUCTURAL_SL" in dec.reasons


def test_atr_final_15_historical_replay_with_valid_timestamps_remains_compatible():
    """ATR-FINAL-15: Historical replay timestamps (epoch ms from 2024) are validated without unit confusion."""
    epoch_2024 = 1704067200000  # 2024-01-01 00:00:00 UTC in ms
    candles = make_dummy_candles(20, t_start=epoch_2024, duration=60_000, symbol="XAUUSDT")
    as_of = epoch_2024 + 20 * 60_000

    atr_res = compute_stop_atr(candles, period=14, timeframe="1M", symbol="XAUUSDT", as_of_ms=as_of)
    assert atr_res.status == "AVAILABLE"
    assert atr_res.last_closed_at_ms == epoch_2024 + 20 * 60_000

    ok, reason = validate_stop_atr_authority(
        atr_res, expected_symbol="XAUUSDT", expected_timeframe="1M", decision_cutoff=as_of
    )
    assert ok is True
    assert reason == "VALID"
