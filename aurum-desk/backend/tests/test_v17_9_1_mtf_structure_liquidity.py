"""
AURUM Desk V17.9.1 — Unit, Property, Integration & Negative Control Suite
=========================================================================
Covers Sections 3, 4, 5, 6, 7, 8, and 10 of V17.9.1:
  - Synchronized closed-candle time & causality (`normalize_market_bars`,
    `get_closed_bars_asof`, `build_mtf_bar_snapshot`, `validate_causal_window`)
  - Causal non-repainting market structure (`detect_confirmed_swings`,
    `classify_swing_sequence`, `detect_structure_break`, `update_structure_state`,
    `build_multi_timeframe_structure`)
  - Causal liquidity & sweep engine (`derive_reference_liquidity_levels`,
    `cluster_equal_high_low`, `detect_liquidity_sweep`, `update_liquidity_state`,
    `summarize_liquidity_for_evaluator`)
  - Shared evaluator 3-mode parity & bounded LRU context cache
  - Disk guard (`probe_disk_budget`, `reserve_test_artifact_budget`,
    `monitor_disk_pressure`, `finalize_and_prune_ephemeral_artifacts`)
  - Adversarial / negative controls
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

import pytest

from backend.services.htf_structure_service import (
    build_mtf_bar_snapshot,
    build_multi_timeframe_structure,
    classify_swing_sequence,
    detect_confirmed_swings,
    detect_structure_break,
    get_closed_bars_asof,
    normalize_market_bars,
    resample_closed_bars_to_htf,
    update_structure_state,
    validate_causal_window,
)
from backend.services.mtf_structure_liquidity_service import (
    MTF_ALGORITHM_VERSION,
    MtfContextCache,
    MtfDataQualityState,
    MtfStructureLiquidityConfig,
    attach_context_to_evaluation,
    build_mtf_context,
    compare_actual_consumed_decisions,
    evaluate_common_decision,
    evaluate_mtf_eligibility_for_direction,
    run_v17_9_1_historical_ab_backtest,
    serialize_decision_evidence,
)
from backend.services.scalp_m1_types import M1ScalpConfig, ModelIdentifier
from backend.services.shared_strategy_evaluator import (
    DecisionStatus,
    SharedStrategyEvaluator,
    build_market_decision_context,
    characterize_production_baseline_decision,
    compare_decision_parity,
)
from backend.services.smc_liquidity_service import (
    cluster_equal_high_low,
    derive_reference_liquidity_levels,
    detect_liquidity_sweep,
    summarize_liquidity_for_evaluator,
    update_liquidity_state,
)
from tools.test_resource_guard import (
    TestResourceGuard,
    finalize_and_prune_ephemeral_artifacts,
    monitor_disk_pressure,
    probe_disk_budget,
    reserve_test_artifact_budget,
)


def _make_bar_dict(
    open_dt: datetime,
    tf_sec: int,
    o: float,
    h: float,
    l: float,
    c: float,
    *,
    is_closed: bool = True,
    symbol: str = "XAUUSDT",
    venue: str = "BITGET",
    volume: float = 120.0,
) -> Dict[str, Any]:
    close_dt = open_dt + timedelta(seconds=tf_sec)
    return {
        "symbol": symbol,
        "venue": venue,
        "open_time": int(open_dt.timestamp() * 1000),
        "close_time": int(close_dt.timestamp() * 1000),
        "available_at": int(close_dt.timestamp() * 1000),
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": volume,
        "is_closed": is_closed,
    }


def _generate_bullish_wave_m15_bars(
    start_dt: datetime,
    count: int = 48,
    base_price: float = 2650.0,
) -> List[Dict[str, Any]]:
    """
    Deterministic closed M15 wave series producing confirmed Swing Highs and Swing Lows
    with HH + HL and closed-candle bullish BOS.
    """
    # Wave pattern offsets every 6 bars: up -> pullback -> higher up -> higher pullback -> breakout
    offsets = [
        0.0, 1.0, 2.5, 1.5, 0.8, 0.4,   # Initial Swing High at idx 2 (+2.5), Swing Low at idx 5 (+0.4)
        1.2, 2.2, 3.8, 2.8, 1.8, 1.2,   # HH at idx 8 (+3.8), HL at idx 11 (+1.2)
        2.5, 3.9, 5.2, 4.1, 3.0, 2.4,   # BOS above +3.8 at idx 13/14, HH at idx 14 (+5.2), HL at idx 17 (+2.4)
        3.6, 5.5, 6.8, 5.6, 4.4, 3.8,   # BOS above +5.2 at idx 19/20, HH at idx 20 (+6.8), HL at idx 23 (+3.8)
    ]
    bars: List[Dict[str, Any]] = []
    for i in range(count):
        cycle = i // len(offsets)
        off = offsets[i % len(offsets)] + cycle * 4.0
        dt = start_dt + timedelta(minutes=15 * i)
        c_px = round(base_price + off, 2)
        o_px = round(c_px - 0.30, 2)
        h_px = round(c_px + 0.40, 2)
        l_px = round(o_px - 0.35, 2)
        bars.append(_make_bar_dict(dt, 900, o_px, h_px, l_px, c_px))
    return bars


# ============================================================================
# SECTION 3 TESTS — DATA / CAUSALITY & AS-OF CUTOFF
# ============================================================================

def test_01_normalize_market_bars_valid_and_negative_controls():
    base_dt = datetime(2026, 3, 25, 0, 0, tzinfo=timezone.utc)
    raw = _generate_bullish_wave_m15_bars(base_dt, count=12)
    norm = normalize_market_bars("XAUUSDT", "M15", raw, {"venue": "BITGET"})
    assert norm.is_valid
    assert norm.quality_state == MtfDataQualityState.VALID
    assert len(norm.bars) == 12

    # Negative Control 1: Wrong symbol must fail closed
    bad_sym = normalize_market_bars("BTCUSDT", "M15", raw, {"venue": "BITGET"})
    assert not bad_sym.is_valid
    assert bad_sym.quality_state == MtfDataQualityState.SOURCE_MISMATCH
    assert any("SYMBOL_MISMATCH" in r for r in bad_sym.rejection_reasons)

    # Negative Control 2: Wrong venue must fail closed
    bad_venue = normalize_market_bars("XAUUSDT", "M15", raw, {"venue": "BINANCE"})
    assert not bad_venue.is_valid
    assert bad_venue.quality_state == MtfDataQualityState.SOURCE_MISMATCH

    # Negative Control 3: Forged / mismatched source hash must fail closed
    bad_hash = normalize_market_bars(
        "XAUUSDT",
        "M15",
        raw,
        {"venue": "BITGET", "expected_source_hash": "forged_hash_000000"},
    )
    assert not bad_hash.is_valid
    assert bad_hash.quality_state == MtfDataQualityState.SOURCE_MISMATCH
    assert any("SOURCE_HASH_MISMATCH" in r for r in bad_hash.rejection_reasons)

    # Negative Control 4: Corrupt OHLC envelope (high < low)
    corrupt_raw = copy.deepcopy(raw)
    corrupt_raw[3]["high"] = corrupt_raw[3]["low"] - 2.0
    corrupt_res = normalize_market_bars("XAUUSDT", "M15", corrupt_raw)
    assert not corrupt_res.is_valid
    assert corrupt_res.quality_state == MtfDataQualityState.CORRUPT_OHLC

    # Negative Control 5: Empty raw bars is NOT a pass
    empty_res = normalize_market_bars("XAUUSDT", "M15", [])
    assert not empty_res.is_valid
    assert empty_res.quality_state == MtfDataQualityState.INSUFFICIENT_HISTORY


def test_02_get_closed_bars_asof_excludes_future_and_partial_candles():
    base_dt = datetime(2026, 3, 25, 0, 0, tzinfo=timezone.utc)
    raw = _generate_bullish_wave_m15_bars(base_dt, count=10)
    # Mark bar index 5 as unclosed (partial)
    raw[5]["is_closed"] = False
    norm = normalize_market_bars("XAUUSDT", "M15", raw)

    # Decision time right at close of bar index 6 (base_dt + 7 * 15m)
    cutoff_dt = base_dt + timedelta(minutes=15 * 7)
    win = get_closed_bars_asof(norm, "M15", cutoff_dt)

    # Bars 0..4 and 6 are closed and <= cutoff_dt (6 bars total); bar 5 is partial; bars 7..9 are future
    assert win.count == 6
    assert win.excluded_partial_count == 1
    assert win.excluded_future_count == 3
    assert win.last_closed_at == cutoff_dt
    for b in win.bars:
        assert b.is_closed
        assert b.close_time <= cutoff_dt
        assert b.available_at <= cutoff_dt


def test_03_build_mtf_bar_snapshot_resampling_and_no_partial_htf_leak():
    base_dt = datetime(2026, 3, 25, 0, 0, tzinfo=timezone.utc)
    # Create 40 M15 bars (10 hours = two complete H4 bars [00:00..04:00, 04:00..08:00] + 2 hours of 3rd H4)
    m15_bars = _generate_bullish_wave_m15_bars(base_dt, count=40)

    # Decision time at 09:30 UTC: 3rd H4 bucket (08:00..12:00 UTC) is NOT closed yet!
    asof_dt = datetime(2026, 3, 25, 9, 30, tzinfo=timezone.utc)
    snap = build_mtf_bar_snapshot(asof_dt, {"symbol": "XAUUSDT", "M15": m15_bars})

    h4_win = snap.get_window("H4")
    h1_win = snap.get_window("H1")
    m15_win = snap.get_window("M15")
    assert h4_win is not None and h1_win is not None and m15_win is not None

    # Only 2 H4 bars (00:00-04:00 and 04:00-08:00) are closed by 09:30 UTC
    assert h4_win.count == 2
    assert h4_win.last_closed_at == datetime(2026, 3, 25, 8, 0, tzinfo=timezone.utc)
    # Only 9 H1 bars (00:00..09:00) are closed by 09:30 UTC (09:00..10:00 is partial!)
    assert h1_win.count == 9
    assert h1_win.last_closed_at == datetime(2026, 3, 25, 9, 0, tzinfo=timezone.utc)
    # 38 M15 bars are closed by 09:30 UTC
    assert m15_win.count == 38

    val = validate_causal_window(snap, asof_dt)
    assert val.is_valid

    # Native vs Aggregated H1 alignment check
    native_h1 = resample_closed_bars_to_htf(m15_win, "H1")
    assert native_h1.count == h1_win.count
    assert native_h1.source_hash == h1_win.source_hash


def test_04_validate_causal_window_detects_future_leak_hard_failure():
    asof_dt = datetime(2026, 3, 25, 4, 0, tzinfo=timezone.utc)
    leaked_payload = {
        "event_id": "forged_future_h4_pivot",
        "confirmed_at": "2026-03-25T08:00:00Z",
        "available_at": "2026-03-25T08:00:00Z",
    }
    res = validate_causal_window(leaked_payload, asof_dt)
    assert not res.is_valid
    assert res.quality_state == MtfDataQualityState.FUTURE_LEAK
    assert "forged_future_h4_pivot" in res.violating_ids
    assert any("FUTURE_LEAK" in r for r in res.reason_codes)


# ============================================================================
# SECTION 4 TESTS — MARKET STRUCTURE (SWINGS, HH/HL/LH/LL, BOS, CHOCH)
# ============================================================================

def test_05_detect_confirmed_swings_right_confirmation_delay_and_prefix_invariance():
    base_dt = datetime(2026, 3, 25, 0, 0, tzinfo=timezone.utc)
    bars = _generate_bullish_wave_m15_bars(base_dt, count=30)
    full_norm = normalize_market_bars("XAUUSDT", "M15", bars)

    full_swings = detect_confirmed_swings(full_norm, left=2, right=2, tolerance_ticks=3)
    assert len(full_swings.events) >= 6

    for sw in full_swings.events:
        # Right-side confirmation delay: confirmed_index == pivot_index + 2
        assert sw.confirmed_index == sw.pivot_index + 2
        assert sw.confirmed_at == sw.pivot_candle_time + timedelta(minutes=15 * (2 + 1))
        assert sw.available_at >= sw.confirmed_at

    # Prefix invariance property test across all checkpoints:
    # Swings confirmed at or before checkpoint `t` when computed on the full 30-bar series
    # MUST be 100% identical to swings computed on the truncated prefix up to `t`!
    for k in range(6, 30):
        checkpoint_dt = base_dt + timedelta(minutes=15 * k)
        prefix_win = get_closed_bars_asof(full_norm, "M15", checkpoint_dt)
        prefix_swings = detect_confirmed_swings(prefix_win, left=2, right=2, tolerance_ticks=3)
        from_full_asof = [e for e in full_swings.events if e.confirmed_at <= checkpoint_dt]
        assert [e.to_dict() for e in prefix_swings.events] == [e.to_dict() for e in from_full_asof]


def test_06_horizontal_tie_swings_and_insufficient_history():
    base_dt = datetime(2026, 3, 25, 0, 0, tzinfo=timezone.utc)
    # Build 8 bars where bars 2 and 3 have an exact equal high tie (2655.00)
    highs = [2650.0, 2652.0, 2655.0, 2655.0, 2651.0, 2649.0, 2648.0, 2647.0]
    raw = [
        _make_bar_dict(base_dt + timedelta(minutes=15 * i), 900, h - 1.0, h, h - 2.0, h - 0.5)
        for i, h in enumerate(highs)
    ]
    swings = detect_confirmed_swings(raw, left=2, right=2, tolerance_ticks=3)
    # Only the first bar of the flat plateau (index 2) is emitted; no duplicate pivot at index 3
    assert len(swings.highs) == 1
    assert swings.highs[0].pivot_index == 2
    assert swings.highs[0].is_equal_tie is True

    # Insufficient swings -> STRUCTURE_UNDETERMINED (never forced to BULLISH/BEARISH)
    st = classify_swing_sequence(swings, "M15")
    assert st.trend == "STRUCTURE_UNDETERMINED"


def test_07_bos_vs_choch_and_wick_only_false_break_rejection():
    base_dt = datetime(2026, 3, 25, 0, 0, tzinfo=timezone.utc)
    bars = _generate_bullish_wave_m15_bars(base_dt, count=24)

    # First verify bullish BOS on closed candles
    swings = detect_confirmed_swings(bars, left=2, right=2, tolerance_ticks=3)
    state = classify_swing_sequence(swings, "M15")
    assert state.trend == "BULLISH"

    breaks = detect_structure_break(bars, state)
    bos_events = [b for b in breaks.events if b.break_type == "BOS_BULLISH"]
    assert len(bos_events) >= 1
    # Ensure idempotency: no broken_swing_id appears more than once
    broken_ids = [b.broken_swing_id for b in breaks.events]
    assert len(broken_ids) == len(set(broken_ids))

    # Now append a candle that wicks 1.50 USD above the latest Swing High but closes BELOW it (wick-only false break)
    last_sh_price = state.last_swing_high.price
    wick_bar_dt = base_dt + timedelta(minutes=15 * 24)
    bars_with_wick = list(bars) + [
        _make_bar_dict(
            wick_bar_dt,
            900,
            last_sh_price - 0.50,
            last_sh_price + 1.50,  # Wick pierces high
            last_sh_price - 0.80,
            last_sh_price - 0.20,  # Close stays below high!
        )
    ]
    swings_w = detect_confirmed_swings(bars_with_wick, left=2, right=2, tolerance_ticks=3)
    state_w = classify_swing_sequence(swings_w, "M15")
    breaks_w = detect_structure_break(bars_with_wick, state_w)

    # The wick-only pierce must be recorded in wick_only_rejections and NOT as a BOS!
    assert any(r["swing_id"] == state.last_swing_high.event_id for r in breaks_w.wick_only_rejections)
    assert not any(b.broken_swing_id == state.last_swing_high.event_id for b in breaks_w.events)

    # Now append a candle that closes decisively BELOW the protected low -> triggers CHOCH_BEARISH
    prot_low_px = state_w.protected_low.price
    choch_bar_dt = base_dt + timedelta(minutes=15 * 25)
    bars_with_choch = list(bars_with_wick) + [
        _make_bar_dict(
            choch_bar_dt,
            900,
            prot_low_px + 0.20,
            prot_low_px + 0.30,
            prot_low_px - 1.20,
            prot_low_px - 0.80,  # Body closes 80 ticks below protected swing low
        )
    ]
    breaks_choch = detect_structure_break(bars_with_choch, state_w)
    assert any(b.break_type == "CHOCH_BEARISH" for b in breaks_choch.events)
    assert breaks_choch.resulting_trend == "BEARISH"


# ============================================================================
# SECTION 5 TESTS — LIQUIDITY POOLS, PDH/PDL, SWEEPS & STATE MACHINE
# ============================================================================

def test_08_cluster_equal_high_low_bounded_diameter_and_tolerance_boundary():
    base_dt = datetime(2026, 3, 25, 0, 0, tzinfo=timezone.utc)
    # Create synthetic confirmed swing highs to test exact tolerance boundary (5 ticks = $0.05)
    # Swing 1: 2660.00, Swing 2: 2660.05 (+5 ticks -> inside tolerance=5),
    # Swing 3: 2660.09 (+9 ticks from anchor -> must NOT chain transitively into cluster!)
    from backend.services.mtf_structure_liquidity_service import SwingEvent

    sw1 = SwingEvent(
        event_id="sw_h_1",
        algorithm_version=MTF_ALGORITHM_VERSION,
        symbol="XAUUSDT",
        timeframe="M15",
        swing_type="SWING_HIGH",
        price=2660.00,
        pivot_index=2,
        confirmed_index=4,
        pivot_bar_id="b2",
        confirming_bar_id="b4",
        pivot_candle_time=base_dt + timedelta(minutes=30),
        detected_at=base_dt + timedelta(minutes=75),
        confirmed_at=base_dt + timedelta(minutes=75),
        available_at=base_dt + timedelta(minutes=75),
        left_bars=2,
        right_bars=2,
        tolerance_ticks=3,
    )
    sw2 = SwingEvent(
        event_id="sw_h_2",
        algorithm_version=MTF_ALGORITHM_VERSION,
        symbol="XAUUSDT",
        timeframe="M15",
        swing_type="SWING_HIGH",
        price=2660.05,  # Exactly 5 ticks above sw1
        pivot_index=8,
        confirmed_index=10,
        pivot_bar_id="b8",
        confirming_bar_id="b10",
        pivot_candle_time=base_dt + timedelta(minutes=120),
        detected_at=base_dt + timedelta(minutes=165),
        confirmed_at=base_dt + timedelta(minutes=165),
        available_at=base_dt + timedelta(minutes=165),
        left_bars=2,
        right_bars=2,
        tolerance_ticks=3,
    )
    sw3_chain = SwingEvent(
        event_id="sw_h_3",
        algorithm_version=MTF_ALGORITHM_VERSION,
        symbol="XAUUSDT",
        timeframe="M15",
        swing_type="SWING_HIGH",
        price=2660.09,  # 4 ticks from sw2, but 9 ticks from sw1 (> 5 tick max diameter!)
        pivot_index=14,
        confirmed_index=16,
        pivot_bar_id="b14",
        confirming_bar_id="b16",
        pivot_candle_time=base_dt + timedelta(minutes=210),
        detected_at=base_dt + timedelta(minutes=255),
        confirmed_at=base_dt + timedelta(minutes=255),
        available_at=base_dt + timedelta(minutes=255),
        left_bars=2,
        right_bars=2,
        tolerance_ticks=3,
    )

    pools_tol5 = cluster_equal_high_low([sw1, sw2, sw3_chain], tolerance_ticks=5, min_touches=2)
    assert len(pools_tol5.pools) == 1
    pool = pools_tol5.pools[0]
    assert pool.pool_type == "EQH"
    assert pool.touch_count == 2
    assert pool.member_swing_ids == ("sw_h_1", "sw_h_2")
    assert pool.diameter_ticks <= 5

    # Negative Control: If tolerance is 4 ticks, 5-tick difference (sw1 vs sw2) must NOT cluster
    pools_tol4 = cluster_equal_high_low([sw1, sw2], tolerance_ticks=4, min_touches=2)
    assert len(pools_tol4.pools) == 0


def test_09_pdh_pdl_completed_prior_day_only_vs_not_available():
    # Case A: Bars only from the current UTC day (2026-03-25) -> PDH/PDL MUST be NOT_AVAILABLE
    day2_start = datetime(2026, 3, 25, 0, 0, tzinfo=timezone.utc)
    same_day_bars = _generate_bullish_wave_m15_bars(day2_start, count=24)
    asof_same_day = datetime(2026, 3, 25, 6, 0, tzinfo=timezone.utc)

    ctx_same_day = build_mtf_context(asof_same_day, {"symbol": "XAUUSDT", "M15": same_day_bars})
    assert ctx_same_day.liquidity_context.pdh_pdl_status == "NOT_AVAILABLE"
    assert ctx_same_day.liquidity_context.pdh_price is None
    assert ctx_same_day.liquidity_context.pdl_price is None

    # Case B: Full prior UTC session (2026-03-24) + current day (2026-03-25) -> PDH/PDL AVAILABLE
    # and strictly equals prior day's high/low, ignoring higher prices on current day!
    day1_start = datetime(2026, 3, 24, 0, 0, tzinfo=timezone.utc)
    day1_bars = _generate_bullish_wave_m15_bars(day1_start, count=96, base_price=2620.0)
    expected_pdh = max(b["high"] for b in day1_bars)
    expected_pdl = min(b["low"] for b in day1_bars)

    # Current day trades much higher (2700.0) -> must NEVER pollute PDH!
    day2_higher_bars = _generate_bullish_wave_m15_bars(day2_start, count=24, base_price=2700.0)
    combined_bars = day1_bars + day2_higher_bars

    ctx_two_days = build_mtf_context(asof_same_day, {"symbol": "XAUUSDT", "M15": combined_bars})
    assert ctx_two_days.liquidity_context.pdh_pdl_status == "AVAILABLE"
    assert ctx_two_days.liquidity_context.pdh_price == expected_pdh
    assert ctx_two_days.liquidity_context.pdl_price == expected_pdl


def test_10_liquidity_sweep_detection_and_lifecycle_state_machine():
    base_dt = datetime(2026, 3, 25, 0, 0, tzinfo=timezone.utc)
    bars = _generate_bullish_wave_m15_bars(base_dt, count=24, base_price=2650.0)
    swings = detect_confirmed_swings(bars, left=2, right=2, tolerance_ticks=3)
    st = classify_swing_sequence(swings, "M15")
    breaks = detect_structure_break(bars, st)
    asof_base = base_dt + timedelta(minutes=15 * 24)
    s_ctx = update_structure_state(None, swings, breaks, asof=asof_base, total_bars_count=len(bars))

    snap_base = build_mtf_bar_snapshot(asof_base, {"symbol": "XAUUSDT", "M15": bars})
    levels = derive_reference_liquidity_levels(snap_base, s_ctx)
    buy_levels = [l for l in levels.levels if l.side == "BUY_SIDE" and l.state == "ACTIVE"]
    assert len(buy_levels) >= 1
    target_lvl = buy_levels[-1]

    # Append 3 test bars after target_lvl is available:
    # Bar 24: Touch-only (wick pierces by +1 tick < min_penetration_ticks=2, closes below) -> TOUCHED
    # Bar 25: Valid BUY_SIDE_SWEEP (wick pierces by +8 ticks >= 2, closes 5 ticks below level) -> SWEPT
    # Bar 26: Second attempt to sweep the same already-swept level -> must NOT emit duplicate sweep!
    lvl_px = target_lvl.price
    bar_touch = _make_bar_dict(
        base_dt + timedelta(minutes=15 * 24),
        900,
        lvl_px - 0.30,
        round(lvl_px + 0.01, 2),  # +1 tick touch only
        lvl_px - 0.50,
        lvl_px - 0.15,
    )
    bar_sweep = _make_bar_dict(
        base_dt + timedelta(minutes=15 * 25),
        900,
        lvl_px - 0.15,
        round(lvl_px + 0.08, 2),  # +8 ticks penetration
        lvl_px - 0.40,
        round(lvl_px - 0.05, 2),  # Reclaims & closes 5 ticks below level
    )
    bar_repeat_sweep = _make_bar_dict(
        base_dt + timedelta(minutes=15 * 26),
        900,
        lvl_px - 0.10,
        round(lvl_px + 0.12, 2),
        lvl_px - 0.35,
        round(lvl_px - 0.06, 2),
    )

    # Check state after touch bar only
    bars_after_touch = bars + [bar_touch]
    asof_touch = base_dt + timedelta(minutes=15 * 25)
    swp_touch = detect_liquidity_sweep(bars_after_touch, levels)
    assert not any(s.swept_level_id == target_lvl.level_id for s in swp_touch.events)
    assert any(t["level_id"] == target_lvl.level_id for t in swp_touch.touches)
    lctx_touch = update_liquidity_state(levels, swp_touch.touches, swp_touch, breaks, asof=asof_touch)
    lvl_after_touch = next(l for l in lctx_touch.levels if l.level_id == target_lvl.level_id)
    assert lvl_after_touch.state == "TOUCHED"

    # Check state after sweep bar + repeat bar
    bars_after_sweep = bars + [bar_touch, bar_sweep, bar_repeat_sweep]
    asof_sweep = base_dt + timedelta(minutes=15 * 27)
    swp_all = detect_liquidity_sweep(bars_after_sweep, levels)
    matching_sweeps = [s for s in swp_all.events if s.swept_level_id == target_lvl.level_id]
    # Swept exactly ONCE (bar_repeat_sweep cannot re-sweep a consumed level)
    assert len(matching_sweeps) == 1
    assert matching_sweeps[0].side == "BUY_SIDE_SWEEP"
    assert matching_sweeps[0].implied_reversal_direction == "BEARISH"
    assert matching_sweeps[0].swept_ticks == 8

    lctx_swept = update_liquidity_state(levels, swp_all.touches, swp_all, breaks, asof=asof_sweep)
    lvl_after_sweep = next(l for l in lctx_swept.levels if l.level_id == target_lvl.level_id)
    assert lvl_after_sweep.state == "SWEPT"
    # Terminal state absorbing: attempting to update again with touches does not revert SWEPT to TOUCHED
    lctx_reupdate = update_liquidity_state(lctx_swept, swp_all.touches, (), (), asof=asof_sweep)
    lvl_reupdate = next(l for l in lctx_reupdate.levels if l.level_id == target_lvl.level_id)
    assert lvl_reupdate.state == "SWEPT"


# ============================================================================
# SECTION 6 & 7 TESTS — CACHE, SHARED EVALUATOR & 3-MODE PARITY
# ============================================================================

def test_11_mtf_context_cache_cold_vs_warm_equivalence_and_invalidation():
    base_dt = datetime(2026, 3, 25, 0, 0, tzinfo=timezone.utc)
    m15_bars = _generate_bullish_wave_m15_bars(base_dt, count=64)
    asof_dt = base_dt + timedelta(minutes=15 * 64)
    cache = MtfContextCache(max_entries=32)
    cfg = MtfStructureLiquidityConfig()

    # Cold compute
    ctx_cold = build_mtf_context(asof_dt, {"symbol": "XAUUSDT", "M15": m15_bars}, config=cfg, cache=cache)
    assert ctx_cold.cache_hit is False
    assert cache.misses == 3

    # Warm compute with identical inputs
    ctx_warm = build_mtf_context(asof_dt, {"symbol": "XAUUSDT", "M15": m15_bars}, config=cfg, cache=cache)
    assert ctx_warm.cache_hit is True
    assert ctx_cold.mtf_context_hash == ctx_warm.mtf_context_hash
    assert ctx_cold.to_summary_dict() == ctx_warm.to_summary_dict()

    # Invalidation on config change
    cfg_changed = MtfStructureLiquidityConfig(swing_left_bars_m15=3, swing_right_bars_m15=3)
    ctx_cfg_changed = build_mtf_context(asof_dt, {"symbol": "XAUUSDT", "M15": m15_bars}, config=cfg_changed, cache=cache)
    assert ctx_cfg_changed.mtf_context_hash != ctx_cold.mtf_context_hash

    # Invalidation on source data perturbation
    perturbed_bars = copy.deepcopy(m15_bars)
    perturbed_bars[20]["high"] += 3.0
    ctx_perturbed = build_mtf_context(asof_dt, {"symbol": "XAUUSDT", "M15": perturbed_bars}, config=cfg, cache=cache)
    assert ctx_perturbed.mtf_context_hash != ctx_cold.mtf_context_hash


def _build_full_mtf_decision_inputs(policy_overrides: Dict[str, Any] | None = None) -> Dict[str, Any]:
    base_dt = datetime(2026, 3, 25, 0, 0, tzinfo=timezone.utc)
    m15_bars = _generate_bullish_wave_m15_bars(base_dt, count=64, base_price=2640.0)
    asof_dt = base_dt + timedelta(minutes=15 * 64)
    asof_ms = int(asof_dt.timestamp() * 1000)

    # Generate closed M5 and M1 bars aligned up to asof_dt
    m5_bars = []
    for i in range(60):
        dt = asof_dt - timedelta(minutes=5 * (60 - i))
        px = round(2650.0 + 0.15 * i, 2)
        m5_bars.append(_make_bar_dict(dt, 300, px - 0.2, px + 0.3, px - 0.4, px))

    m1_bars = []
    for i in range(40):
        dt = asof_dt - timedelta(minutes=(40 - i))
        px = round(2655.0 + 0.10 * i, 2)
        m1_bars.append(_make_bar_dict(dt, 60, px - 0.1, px + 0.25, px - 0.2, px))

    h1_win = resample_closed_bars_to_htf(get_closed_bars_asof(m15_bars, "M15", asof_dt), "H1")
    h4_win = resample_closed_bars_to_htf(get_closed_bars_asof(m15_bars, "M15", asof_dt), "H4")

    return {
        "symbol": "XAUUSDT",
        "decision_timestamp_ms": asof_ms,
        "m1_candles": m1_bars,
        "m5_candles": m5_bars,
        "m15_candles": m15_bars,
        "h1_candles": [b.to_dict() for b in h1_win.bars],
        "h4_candles": [b.to_dict() for b in h4_win.bars],
        "bid": 2658.90,
        "ask": 2659.05,
        "policy_config": dict(policy_overrides or {}),
    }


def test_12_baseline_flag_off_invariance_and_3_mode_consumed_parity():
    inputs_off = _build_full_mtf_decision_inputs()
    ctx_off = build_market_decision_context(**inputs_off)

    # Verify MTF context is attached in observe-only mode by default
    assert ctx_off.mtf_context_id is not None
    assert ctx_off.mtf_context_hash is not None
    assert ctx_off.mtf_algorithm_version == "17.9.1"

    model_id = ModelIdentifier.SMC_M1_FVG_CONTINUATION.value
    # Compare against IndependentPreRefactorBaselineOracle (baseline flag=OFF invariance)
    oracle_snap = characterize_production_baseline_decision(ctx_off, model_id, "LONG")
    prod_snap = SharedStrategyEvaluator.evaluate_model_direction(
        ctx_off, model_id, "LONG", adapter_origin="PRODUCTION_ORCHESTRATOR"
    )
    shadow_snap = SharedStrategyEvaluator.evaluate_model_direction(
        ctx_off, model_id, "LONG", adapter_origin="MARKET_SHADOW_ADAPTER"
    )
    bt_snap = SharedStrategyEvaluator.evaluate_model_direction(
        ctx_off, model_id, "LONG", adapter_origin="CHRONOLOGICAL_BACKTEST"
    )

    parity_vs_oracle = compare_decision_parity(oracle_snap, prod_snap)
    assert parity_vs_oracle["is_match"], f"Baseline OFF drifted from oracle: {parity_vs_oracle['mismatches']}"

    parity_3_way = compare_actual_consumed_decisions(prod_snap, shadow_snap, bt_snap, independent_oracle=oracle_snap)
    assert parity_3_way["is_parity"], f"3-way parity failed: {parity_3_way['mismatches']}"
    assert parity_3_way["status"] == "PASS"

    # Negative Control: Circular self-extraction (passing identical object reference) must fail closed!
    self_extract_report = compare_actual_consumed_decisions(prod_snap, prod_snap, bt_snap)
    assert not self_extract_report["is_parity"]
    assert self_extract_report["status"] == "PARITY_NOT_ESTABLISHED"
    assert self_extract_report["reason"] == "SELF_EXTRACTED_IDENTICAL_OBJECT_INSTANCE"


def test_13_mtf_eligibility_flag_on_filtering_and_ablation_modes():
    # Build bullish MTF context and test LONG (aligned) vs SHORT (conflicting)
    inputs_on = _build_full_mtf_decision_inputs(
        policy_overrides={
            "mtf_structure_observe_only": False,
            "mtf_structure_eligibility_enabled": True,
            "mtf_ablation_mode": "FULL",
        }
    )
    ctx_on = build_market_decision_context(**inputs_on)
    assert ctx_on.mtf_context is not None
    assert ctx_on.mtf_context.mtf_structure.m15.trend == "BULLISH"

    # Direct eligibility check on bullish MTF structure:
    ok_long, rej_long, _ = evaluate_mtf_eligibility_for_direction("LONG", ctx_on.mtf_context, ablation_mode="M15_ONLY")
    assert ok_long is True and rej_long is None

    ok_short, rej_short, _ = evaluate_mtf_eligibility_for_direction("SHORT", ctx_on.mtf_context, ablation_mode="M15_ONLY")
    assert ok_short is False
    assert rej_short == "MTF_STRUCTURE_FILTER_REJECT"

    # Missing MTF data when eligibility is ON must reject with MTF_MISSING_DATA
    ok_missing, rej_missing, _ = evaluate_mtf_eligibility_for_direction("LONG", None, ablation_mode="FULL")
    assert ok_missing is False
    assert rej_missing == "MTF_MISSING_DATA"

    # Evidence serialization determinism
    snap = evaluate_common_decision(ctx_on, strategy_config={"mtf_structure_eligibility_enabled": True})
    ev1 = serialize_decision_evidence(snap, ctx_on.mtf_context)
    ev2 = serialize_decision_evidence(snap, ctx_on.mtf_context)
    assert ev1["evidence_payload_hash"] == ev2["evidence_payload_hash"]
    assert ev1["mtf_evidence"]["mtf_context_hash"] == ctx_on.mtf_context_hash


# ============================================================================
# SECTION 10 TESTS — DISK GUARD & EPHEMERAL ARTIFACT PRUNING
# ============================================================================

def test_14_disk_guard_budget_probe_reservation_and_safe_ephemeral_pruning(tmp_path: Path):
    budget = probe_disk_budget(workdir=tmp_path, artifact_root=tmp_path)
    assert budget.total_bytes > 0
    assert budget.free_bytes > 0
    assert budget.effective_floor_bytes > 0
    assert budget.status in ("OK", "WARN_LOW_HEADROOM")

    # Small reservation succeeds
    permit = reserve_test_artifact_budget("unit_smoke", budget=budget, requested_bytes=1024 * 1024)
    assert permit.permitted is True
    assert permit.status == "PERMITTED"

    # Impossible reservation (requesting more than total disk capacity) blocks BEFORE crossing threshold
    blocked = reserve_test_artifact_budget("huge_backtest", budget=budget, requested_bytes=budget.total_bytes)
    assert blocked.permitted is False
    assert blocked.status == "DISK_GUARD_BLOCKED"

    pressure = monitor_disk_pressure(interval=0.5, workdir=tmp_path, artifact_root=tmp_path)
    assert pressure["status"] in ("NORMAL", "HIGH_PRESSURE_THROTTLE")

    guard = TestResourceGuard(
        run_id="test_v17_9_1_disk_guard",
        artifact_dir=tmp_path / "artifacts",
        tmp_root=tmp_path / "temp_workers",
        reserved_floor_override_bytes=1024 * 1024,
    )
    w_dir = guard.create_worker_temp_dir("shard_01")
    ephemeral_file = w_dir / "ephemeral.tmp"
    ephemeral_file.write_text("temporary shard data", encoding="utf-8")
    assert ephemeral_file.exists()

    report = guard.finalize_and_prune_ephemeral_artifacts()
    assert report.status == "PASS"
    assert report.bytes_pruned_ephemeral > 0
    assert not w_dir.exists()


def test_15_three_runtime_adapters_production_shadow_backtest_identical_consumed_decisions():
    """
    Section 6 & 8.B: Execute actual runtime adapter entry points for Production,
    MARKET_SHADOW (`MarketShadowService.evaluate_shared_decision_snapshot`), and Backtest
    (`evaluate_production_parity_model_snapshot`) on identical frozen MarketDecisionContext.
    """
    from backend.services.market_shadow_service import MarketShadowService
    from backend.services.v17_8_backtest_runner import evaluate_production_parity_model_snapshot

    inputs = _build_full_mtf_decision_inputs()
    ctx = build_market_decision_context(**inputs)
    model_id = ModelIdentifier.SMC_M1_FVG_CONTINUATION.value
    pref_dir = SharedStrategyEvaluator.resolve_canonical_preferred_direction(ctx)

    prod_snap = SharedStrategyEvaluator.evaluate_decision(
        market_snapshot=ctx,
        strategy_config={
            "model_id": model_id,
            "direction": pref_dir,
            "adapter_origin": "PRODUCTION_ORCHESTRATOR",
        },
    )
    shadow_svc = MarketShadowService(symbol="XAUUSDT")
    shadow_snap = shadow_svc.evaluate_shared_decision_snapshot(
        context=ctx, model_id=model_id, direction=pref_dir
    )
    bt_res = evaluate_production_parity_model_snapshot(
        model_id=model_id, context=ctx, direction=pref_dir
    )
    bt_snap = bt_res["shared_decision_snapshot"]

    report = compare_actual_consumed_decisions(prod_snap, shadow_snap, bt_snap)
    assert report["is_parity"], f"Runtime 3-adapter parity mismatch: {report['mismatches']}"
    assert prod_snap.mtf_context_hash == shadow_snap.mtf_context_hash == bt_snap["mtf_context_hash"]
    assert prod_snap.decision_fingerprint == shadow_snap.decision_fingerprint == bt_snap["decision_fingerprint"]


def test_16_negative_controls_stale_htf_missing_gaps_and_conflicting_duplicates():
    base_dt = datetime(2026, 3, 25, 0, 0, tzinfo=timezone.utc)
    m15_bars = _generate_bullish_wave_m15_bars(base_dt, count=32)

    # 1. Stale H1/H4 context: decision_time is 12 hours after last closed bar
    stale_decision_dt = base_dt + timedelta(hours=20)
    stale_snap = build_mtf_bar_snapshot(stale_decision_dt, {"symbol": "XAUUSDT", "M15": m15_bars})
    assert stale_snap.quality_state == MtfDataQualityState.STALE_HTF
    assert any("STALE_TIMEFRAME" in r for r in stale_snap.rejection_reasons)

    # 2. Missing M15 candle gap detection (drop bar index 10)
    gapped_bars = m15_bars[:10] + m15_bars[11:]
    norm_gap = normalize_market_bars("XAUUSDT", "M15", gapped_bars)
    assert norm_gap.missing_gap_count == 1
    assert norm_gap.quality_state == MtfDataQualityState.MISSING_BARS

    # 3. Conflicting duplicate timestamp must fail closed as CORRUPT_OHLC
    dup_bars = copy.deepcopy(m15_bars[:10])
    conflicting_bar = copy.deepcopy(dup_bars[4])
    conflicting_bar["close"] = round(conflicting_bar["close"] + 0.25, 2)
    conflicting_bar["high"] = max(conflicting_bar["high"], conflicting_bar["close"])
    dup_bars.append(conflicting_bar)
    norm_dup = normalize_market_bars("XAUUSDT", "M15", dup_bars)
    assert not norm_dup.is_valid
    assert norm_dup.quality_state == MtfDataQualityState.CORRUPT_OHLC
    assert any("CONFLICTING_DUPLICATE_BAR" in r for r in norm_dup.rejection_reasons)

    # 4. Out-of-order input when reject_out_of_order=True
    reversed_bars = list(reversed(m15_bars[:8]))
    norm_rev = normalize_market_bars("XAUUSDT", "M15", reversed_bars, {"reject_out_of_order": True})
    assert not norm_rev.is_valid
    assert norm_rev.quality_state == MtfDataQualityState.BAD_TIME_ALIGNMENT


def test_17_exact_close_time_boundary_and_expired_pool_non_recycling():
    base_dt = datetime(2026, 3, 25, 0, 0, tzinfo=timezone.utc)
    bars = _generate_bullish_wave_m15_bars(base_dt, count=20)
    exact_close_19 = base_dt + timedelta(minutes=15 * 20)

    # Exactly at close_time of bar 19 -> all 20 bars included
    win_exact = get_closed_bars_asof(bars, "M15", exact_close_19)
    assert win_exact.count == 20

    # 1 millisecond before close_time of bar 19 -> bar 19 is NOT closed yet (19 bars included)
    win_minus_1ms = get_closed_bars_asof(bars, "M15", exact_close_19 - timedelta(milliseconds=1))
    assert win_minus_1ms.count == 19

    # Expired pool cannot be swept
    cfg_short_ttl = MtfStructureLiquidityConfig(pool_max_age_bars=2)
    swings = detect_confirmed_swings(bars, left=2, right=2, tolerance_ticks=3)
    st = classify_swing_sequence(swings, "M15")
    breaks = detect_structure_break(bars, st)
    s_ctx = update_structure_state(None, swings, breaks, asof=exact_close_19, total_bars_count=len(bars))
    snap = build_mtf_bar_snapshot(exact_close_19, {"symbol": "XAUUSDT", "M15": bars}, settings=cfg_short_ttl)
    levels = derive_reference_liquidity_levels(snap, s_ctx, policy=cfg_short_ttl)
    expired_levels = [l for l in levels.levels if l.state == "EXPIRED"]
    assert len(expired_levels) >= 1

    # Attempt to sweep an already EXPIRED level -> must produce 0 sweeps!
    exp_lvl = expired_levels[0]
    sweep_attempt_bar = _make_bar_dict(
        exact_close_19,
        900,
        exp_lvl.price - 0.10,
        exp_lvl.price + 0.50,
        exp_lvl.price - 0.50,
        exp_lvl.price - 0.10,
    )
    swp_res = detect_liquidity_sweep(bars + [sweep_attempt_bar], [exp_lvl], sweep_policy=cfg_short_ttl)
    assert len(swp_res.events) == 0


def test_18_worker_permutation_order_invariance_and_future_perturbation_isolation():
    base_dt = datetime(2026, 3, 25, 0, 0, tzinfo=timezone.utc)
    bars_a = _generate_bullish_wave_m15_bars(base_dt, count=48)
    asof_early = base_dt + timedelta(minutes=15 * 24)

    # Perturb bars AFTER asof_early (bars 24..47) with extreme prices
    bars_b = copy.deepcopy(bars_a)
    for idx in range(24, 48):
        bars_b[idx]["high"] += 500.0
        bars_b[idx]["close"] += 450.0

    ctx_a = build_mtf_context(asof_early, {"symbol": "XAUUSDT", "M15": bars_a})
    ctx_b = build_mtf_context(asof_early, {"symbol": "XAUUSDT", "M15": bars_b})

    # Future price perturbations after `asof_early` cannot affect earlier context or hashes!
    assert ctx_a.mtf_context_hash == ctx_b.mtf_context_hash
    assert ctx_a.to_summary_dict() == ctx_b.to_summary_dict()


def test_19_real_historical_ab_backtest_and_ablations():
    """
    Section 7 & 8.B: Execute real historical A/B/C/D backtest on canonical Bitget XAUUSDT
    historical dataset (`backtest/historical_dataset_XAUUSDT.json.gz`), verifying:
      - 100% baseline parity vs IndependentPreRefactorBaselineOracle (`oracle_vs_a_mismatches == 0`)
      - 100% 3-mode consumed decision parity across Production, MARKET_SHADOW, and Backtest (`parity_mismatch_count == 0`)
      - Exact equivalence between `A_BASELINE_OFF` and `B_OBSERVE_ONLY` (`a_vs_b_decision_invariance_mismatches == 0`)
      - Full ablation matrix (`C_ELIGIBILITY_ON_FULL`, `D1_ABLATION_H4_ONLY`, `D2_ABLATION_H1_ONLY`,
        `D3_ABLATION_M15_ONLY`, `D4_ABLATION_STRUCTURE_ONLY`, `D5_ABLATION_LIQUIDITY_ONLY`)
    """
    res = run_v17_9_1_historical_ab_backtest(max_evaluation_points=24)
    assert res["status"] == "PASS"
    assert res["dataset"]["symbol"] == "XAUUSDT"
    assert res["dataset"]["venue"] == "BITGET"
    assert res["dataset"]["evaluation_checkpoints"] > 0
    assert res["parity"]["status"] == "PASS"
    assert res["parity"]["parity_mismatch_count"] == 0
    assert res["parity"]["a_vs_b_decision_invariance_mismatches"] == 0
    assert res["parity"]["oracle_vs_a_mismatches"] == 0

    variants = res["variants"]
    expected_variants = (
        "A_BASELINE_OFF",
        "B_OBSERVE_ONLY_DEFAULT",
        "C_MTF_ELIGIBILITY_ON_FULL",
        "D1_ABLATION_H4_ONLY",
        "D2_ABLATION_H1_ONLY",
        "D3_ABLATION_M15_ONLY",
        "D4_ABLATION_STRUCTURE_ONLY",
        "D5_ABLATION_LIQUIDITY_ONLY",
    )
    for v_key in expected_variants:
        assert v_key in variants
        v_metrics = variants[v_key]
        assert "n_decisions_evaluated" in v_metrics
        assert "n_eligible_trades" in v_metrics
        assert "win_rate" in v_metrics
        assert "profit_factor" in v_metrics
        assert "expectancy_r" in v_metrics
        assert "max_drawdown_r" in v_metrics
        assert "rejection_reasons" in v_metrics
        assert "partition_counts" in v_metrics

    assert variants["A_BASELINE_OFF"]["n_eligible_trades"] == variants["B_OBSERVE_ONLY_DEFAULT"]["n_eligible_trades"]
    assert variants["A_BASELINE_OFF"]["decisions_Set_hash"] == variants["B_OBSERVE_ONLY_DEFAULT"]["decisions_Set_hash"]
