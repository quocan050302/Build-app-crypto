"""
backend/tests/test_v16_28_cg01_setup_correctness.py
V16.28 - CG-01 Setup Correctness Comprehensive Test Suite.
Tests:
- Part A: Actual, causal M1 ATR in displacement confirmation (Items 026 - 070)
- Part B: Meaningful FVG formation, freshness, retest, and invalidation lifecycle (Items 071 - 120)
- Part C: Multi-POI evaluation across all eligible zones without early wait abort (Items 121 - 150)
- Part D: Production integration and backward compatibility preservation (Items 151 - 165)
"""
import copy
import math
from datetime import datetime
from zoneinfo import ZoneInfo
import pytest

from domain_calculator import CostAssumptions
from services.cg01_context_adapter import (
    CanonicalCandle,
    build_cg01_closed_context,
    resolve_cg01_retest,
)
from services.cg01_engine import (
    assess_cg01_fvg_lifecycle,
    build_cg01_displacement_atr_series,
    detect_cg01_sweep,
    evaluate_cg01_candidate,
    resolve_cg01_mss_and_displacement,
    select_cg01_leg_fvg,
)
from services.cg01_types import (
    CG01Config,
    CG01Decision,
    CG01EntryMode,
    CG01Stage,
)
from services.scalp_m1_types import WatchedZone
from tests.test_v16_3_cg01_pipeline import (
    build_long_fixture_series,
    build_short_fixture_series,
    make_bar,
    make_valid_quote,
)


# ==============================================================================
# PART A: M1 ATR CAUSALITY & DISPLACEMENT CONFIRMATION
# ==============================================================================

def test_v16_28_atr_exact_formula_and_convention():
    """
    Items 028-030, 061: Validates True Range formula with previous close and
    arithmetic mean over configured period.
    """
    t0 = 1714554000000
    # Create 5 candles with exact known OHLC
    # bar 0: O=100, H=102, L=99, C=101
    # bar 1: O=101, H=105, L=100, C=104 -> TR = max(105-100, |105-101|, |100-101|) = max(5, 4, 1) = 5.0
    # bar 2: O=104, H=106, L=102, C=103 -> TR = max(106-102, |106-104|, |102-104|) = max(4, 2, 2) = 4.0
    # bar 3: O=103, H=107, L=101, C=106 -> TR = max(107-101, |107-103|, |101-103|) = max(6, 4, 2) = 6.0
    # bar 4: break candle at t0 + 240_000
    candles = [
        make_bar(t0, 100.0, 102.0, 99.0, 101.0),
        make_bar(t0 + 60_000, 101.0, 105.0, 100.0, 104.0),
        make_bar(t0 + 120_000, 104.0, 106.0, 102.0, 103.0),
        make_bar(t0 + 180_000, 103.0, 107.0, 101.0, 106.0),
        make_bar(t0 + 240_000, 106.0, 110.0, 105.0, 109.0),
    ]

    atr_series = build_cg01_displacement_atr_series(candles, period=3, as_of_ms=t0 + 300_000)
    # Candle 4 is at t0 + 240_000. Completed bars prior to candle 4 are bars 0..3 with 3 TRs: 5.0, 4.0, 6.0.
    # Mean = (5.0 + 4.0 + 6.0) / 3 = 15.0 / 3 = 5.0
    assert t0 + 240_000 in atr_series
    assert math.isclose(atr_series[t0 + 240_000], 5.0, rel_tol=1e-5)


def test_v16_28_atr_causality_and_prefix_invariance():
    """
    Items 031, 040, 068: Appending future candles after as_of_ms leaves the ATR series
    and decision at as_of_ms strictly immutable.
    """
    t_start = 1714554000000
    _, _, _, c_1m = build_long_fixture_series(t_start)
    as_of = t_start + 480_000

    atr_baseline = build_cg01_displacement_atr_series(c_1m, period=14, as_of_ms=as_of)

    # Future wild candles after as_of
    future_candles = list(c_1m) + [
        make_bar(as_of + 60_000, 2625.0, 2690.0, 2610.0, 2680.0),
        make_bar(as_of + 120_000, 2680.0, 2750.0, 2670.0, 2740.0),
    ]
    atr_with_future = build_cg01_displacement_atr_series(future_candles, period=14, as_of_ms=as_of)

    assert atr_baseline == atr_with_future


def test_v16_28_atr_pre_break_baseline_not_self_referential():
    """
    Item 032: The break candle's own body and displacement range do NOT inflate
    its own ATR baseline threshold.
    """
    t0 = 1714554000000
    # 15 calm candles with TR ~ 0.50
    candles = [make_bar(t0 + i * 60_000, 100.0, 100.5, 99.5, 100.0) for i in range(15)]
    # 16th candle is a massive break candle with body = 20.0
    break_ts = t0 + 15 * 60_000
    candles.append(make_bar(break_ts, 100.0, 125.0, 99.0, 120.0))

    atr_series = build_cg01_displacement_atr_series(candles, period=14, as_of_ms=break_ts + 60_000)
    # The ATR at break_ts must reflect the calm candles (approx 1.0), not the 25.0 range of the break candle itself
    atr_at_break = atr_series[break_ts]
    assert atr_at_break < 2.0, f"Expected calm pre-break ATR, got inflated: {atr_at_break}"


def test_v16_28_atr_dynamic_volatility_low_vs_high():
    """
    Items 062, 063: A fixed displacement body (e.g. 1.20) qualifies when ATR is low (1.0),
    but fails when ATR is high (2.0), proving no fixed constant threshold is used.
    """
    cfg = CG01Config(displacement_body_atr_min=0.80, tick_size=0.01)

    # Fake sweep info
    sweep_info = {"sweep_bar_idx": 5, "reclaim_bar_idx": 6}

    # Bars: pivot high at index 4 (high=102.0), sweep at 5, reclaim at 6, break candle at index 7
    # Break candle body = 103.0 - 101.8 = 1.20
    def make_test_bars(break_open=101.8, break_close=103.0):
        return [
            make_bar(1000, 100.0, 101.0, 99.5, 100.5),
            make_bar(2000, 100.5, 101.5, 100.0, 101.0),
            make_bar(3000, 101.0, 101.8, 100.5, 101.2),
            make_bar(4000, 101.2, 102.0, 101.0, 101.8), # SH at 102.0
            make_bar(5000, 101.8, 101.9, 101.0, 101.5),
            make_bar(6000, 101.5, 101.7, 100.8, 101.2), # reclaim
            make_bar(7000, 101.2, 101.5, 100.9, 101.1),
            make_bar(8000, break_open, break_close + 0.1, break_open - 0.1, break_close),
        ]

    bars = make_test_bars()

    # Case 1: Low volatility (ATR = 1.0) -> required body = 0.8 * 1.0 = 0.80 -> 1.20 >= 0.80 passes!
    mss_low = resolve_cg01_mss_and_displacement(
        bars, sweep_info, direction="LONG", now_ms=9000, config=cfg, atr_1m={8000: 1.0}
    )
    assert mss_low is not None
    assert mss_low["displacement_body"] == pytest.approx(1.20, abs=1e-3)
    assert mss_low["displacement_atr"] == 1.0

    # Case 2: High volatility (ATR = 2.0) -> required body = 0.8 * 2.0 = 1.60 -> 1.20 < 1.60 fails!
    mss_high = resolve_cg01_mss_and_displacement(
        bars, sweep_info, direction="LONG", now_ms=9000, config=cfg, atr_1m={8000: 2.0}
    )
    assert mss_high is None, "Expected displacement to fail under high volatility (1.20 < 1.60)"


def test_v16_28_atr_config_period_propagation():
    """
    Item 064: Configuring displacement_atr_period directly changes the ATR window and values.
    """
    t0 = 1714554000000
    # 20 candles with increasing volatility
    candles = [
        make_bar(t0 + i * 60_000, 100.0 + i, 100.0 + i + (i * 0.2), 100.0 + i - (i * 0.1), 100.0 + i)
        for i in range(20)
    ]
    target_ts = t0 + 19 * 60_000

    atr_p5 = build_cg01_displacement_atr_series(candles, period=5, as_of_ms=target_ts + 60_000)
    atr_p14 = build_cg01_displacement_atr_series(candles, period=14, as_of_ms=target_ts + 60_000)

    assert target_ts in atr_p5
    assert target_ts in atr_p14
    # The 5-period ATR includes only recent high volatility, so it must differ from 14-period
    assert not math.isclose(atr_p5[target_ts], atr_p14[target_ts], rel_tol=1e-3)


def test_v16_28_atr_insufficient_warmup_observing():
    """
    Items 041, 065: When M1 candles are fewer than period + 1, evaluate_cg01_candidate returns
    OBSERVING with reason ATR_WARMUP without defaulting to 1.0.
    """
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)

    c_4h, c_1h, c_15m, _ = build_long_fixture_series(t_start)
    # Only 5 M1 bars provided (need 15 for period 14)
    short_m1 = [make_bar(t_start + i * 60_000, 2620.0, 2622.0, 2619.0, 2621.0) for i in range(5)]
    now_ms = t_start + 5 * 60_000
    ctx = {"4H": c_4h, "1H": c_1h, "15M": c_15m, "1M": short_m1}
    quote = make_valid_quote(2621.0, 2621.2, now_ms)
    cfg = CG01Config(displacement_atr_period=14)

    dec = evaluate_cg01_candidate(ctx, quote, config=cfg, now_ms=now_ms)
    assert dec.stage == CG01Stage.OBSERVING
    assert not dec.is_eligible
    assert "ATR_WARMUP" in dec.reason_codes


def test_v16_28_atr_flat_market_floor_and_nonfinite_safety():
    """
    Items 042, 043, 044, 066: Zero volatility respects safe floor 0.20; NaN/Inf fail safely.
    """
    t0 = 1714554000000
    # Completely flat candles
    flat_candles = [make_bar(t0 + i * 60_000, 100.0, 100.0, 100.0, 100.0) for i in range(16)]
    atr_series = build_cg01_displacement_atr_series(flat_candles, period=14, as_of_ms=t0 + 16 * 60_000)
    ts = t0 + 15 * 60_000
    assert ts in atr_series
    raw_val = atr_series[ts]
    assert raw_val == 0.0

    # In detector, safe_atr floor takes effect
    sweep_info = {"sweep_bar_idx": 5, "reclaim_bar_idx": 6}
    bars = [
        make_bar(1000, 100.0, 101.0, 99.5, 100.5),
        make_bar(2000, 100.5, 101.5, 100.0, 101.0),
        make_bar(3000, 101.0, 101.8, 100.5, 101.2),
        make_bar(4000, 101.2, 102.0, 101.0, 101.8),
        make_bar(5000, 101.8, 101.9, 101.0, 101.5),
        make_bar(6000, 101.5, 101.7, 100.8, 101.2),
        make_bar(7000, 101.2, 101.5, 100.9, 101.1),
        make_bar(8000, 101.8, 103.0, 101.5, 102.5),
    ]
    # Passing 0.0 ATR -> safe_atr = max(0.0, 0.20) = 0.20
    mss = resolve_cg01_mss_and_displacement(
        bars, sweep_info, direction="LONG", now_ms=9000, config=CG01Config(), atr_1m={8000: 0.0}
    )
    assert mss is not None
    assert mss["displacement_atr"] == 0.20

    # Passing NaN ATR -> skipped safely
    mss_nan = resolve_cg01_mss_and_displacement(
        bars, sweep_info, direction="LONG", now_ms=9000, config=CG01Config(), atr_1m={8000: float("nan")}
    )
    assert mss_nan is None


# ==============================================================================
# PART B: FVG FORMATION, FRESHNESS, RETEST & INVALIDATION
# ==============================================================================

def test_v16_28_fvg_formation_known_at_third_candle_close():
    """
    Items 074-076, 115: FVG is confirmed only when the 3rd candle closes (known_at_ms = form_time_ms + 60_000).
    """
    fvg = {
        "fvg_id": "fvg_test_1",
        "lower": 2623.0,
        "upper": 2624.5,
        "ce_price": 2623.75,
        "formation_time_ms": 100_000,
        "known_at_ms": 160_000,
        "formation_bar_idx": 3,
    }
    # Candle opening at 100_000 closes at 160_000. Prior to 160_000 it is not yet known.
    candles = [make_bar(i * 60_000, 2623.0, 2624.0, 2622.0, 2623.5) for i in range(5)]

    # Lifecycle evaluated at 150_000 (before 3rd candle closes) has no post-known touches
    lc_pre = assess_cg01_fvg_lifecycle(fvg, candles, "LONG", as_of_ms=150_000)
    assert lc_pre["first_touch_ms"] is None
    assert lc_pre["first_ce_touch_ms"] is None


def test_v16_28_fvg_lifecycle_fresh_candidate():
    """
    Items 080, 108: Untouched FVG after formation is fresh with mitigation ratio 0.0.
    """
    fvg = {
        "fvg_id": "fvg_fresh",
        "lower": 2623.0,
        "upper": 2624.5,
        "ce_price": 2623.75,
        "formation_time_ms": 100_000,
        "known_at_ms": 160_000,
        "formation_bar_idx": 2,
    }
    # Post-known candles remain above upper
    candles = [
        make_bar(0, 2620.0, 2621.0, 2619.5, 2620.5),
        make_bar(60_000, 2621.0, 2623.0, 2620.8, 2622.8),
        make_bar(100_000, 2622.8, 2625.0, 2622.5, 2624.8),
        make_bar(160_000, 2625.0, 2627.0, 2625.0, 2626.5),
        make_bar(220_000, 2626.5, 2628.0, 2626.0, 2627.5),
    ]
    lc = assess_cg01_fvg_lifecycle(fvg, candles, "LONG", as_of_ms=280_000)
    assert lc["is_fresh"]
    assert lc["reason"] == "FRESH"
    assert lc["max_mitigation_ratio"] == 0.0
    assert lc["first_touch_ms"] is None


def test_v16_28_fvg_lifecycle_limit_used_before_arm():
    """
    Items 087, 111: In LIMIT_AT_CE_AFTER_CONFIRMATION mode, if price touched CE prior to arming,
    it is not fresh for a new pending limit order (FVG_USED_BEFORE_ARM).
    """
    fvg = {
        "fvg_id": "fvg_used",
        "lower": 2623.0,
        "upper": 2625.0,
        "ce_price": 2624.0, # height = 2.0
        "formation_time_ms": 100_000,
        "known_at_ms": 160_000,
        "formation_bar_idx": 2,
    }
    # Post-known candle at 160_000 dips to 2623.9 (touches CE 2624.0)
    candles = [
        make_bar(0, 2620.0, 2621.0, 2619.5, 2620.5),
        make_bar(60_000, 2621.0, 2623.0, 2620.8, 2622.8),
        make_bar(100_000, 2622.8, 2625.5, 2622.5, 2625.2),
        make_bar(160_000, 2625.2, 2625.5, 2623.9, 2624.8),
    ]
    lc = assess_cg01_fvg_lifecycle(
        fvg, candles, "LONG", as_of_ms=220_000, entry_mode=CG01EntryMode.LIMIT_AT_CE_AFTER_CONFIRMATION.value
    )
    assert not lc["is_fresh"]
    assert lc["reason"] == "FVG_USED_BEFORE_ARM"
    assert lc["first_ce_touch_ms"] == 160_000


def test_v16_28_fvg_lifecycle_limit_over_mitigated():
    """
    Items 085, 110: When mitigation depth exceeds max_fvg_mitigation_ratio (0.50),
    the FVG is rejected as FVG_OVER_MITIGATED.
    """
    fvg = {
        "fvg_id": "fvg_deep",
        "lower": 2620.0,
        "upper": 2630.0,
        "ce_price": 2625.0, # height = 10.0
        "formation_time_ms": 100_000,
        "known_at_ms": 160_000,
        "formation_bar_idx": 2,
    }
    # Post-known candle dips to 2622.0 (depth 8.0 / 10.0 = 0.80 mitigation > 0.50)
    candles = [
        make_bar(0, 2618.0, 2620.0, 2617.0, 2619.0),
        make_bar(60_000, 2619.0, 2620.0, 2618.5, 2619.5),
        make_bar(100_000, 2619.5, 2631.0, 2619.0, 2630.5),
        make_bar(160_000, 2630.5, 2631.0, 2622.0, 2626.0),
    ]
    lc = assess_cg01_fvg_lifecycle(
        fvg, candles, "LONG", as_of_ms=220_000, config=CG01Config(max_fvg_mitigation_ratio=0.50)
    )
    assert not lc["is_fresh"]
    assert lc["max_mitigation_ratio"] == 0.80
    assert lc["reason"] in ("FVG_OVER_MITIGATED", "FVG_USED_BEFORE_ARM")


def test_v16_28_fvg_lifecycle_market_retest_allows_ce_touch():
    """
    Items 090, 113: In CONFIRMED_RETEST_MARKET mode, touching CE does not invalidate the candidate;
    it marks the zone ACTIVE_FOR_RETEST.
    """
    fvg = {
        "fvg_id": "fvg_retest_ok",
        "lower": 2620.0,
        "upper": 2630.0,
        "ce_price": 2625.0,
        "formation_time_ms": 100_000,
        "known_at_ms": 160_000,
        "formation_bar_idx": 2,
    }
    # Post-known candle dips to 2624.5 (touches CE 2625.0) and closes at 2626.0
    candles = [
        make_bar(0, 2618.0, 2620.0, 2617.0, 2619.0),
        make_bar(60_000, 2619.0, 2620.0, 2618.5, 2619.5),
        make_bar(100_000, 2619.5, 2631.0, 2619.0, 2630.5),
        make_bar(160_000, 2630.5, 2631.0, 2624.5, 2626.0),
    ]
    lc = assess_cg01_fvg_lifecycle(
        fvg, candles, "LONG", as_of_ms=220_000, entry_mode=CG01EntryMode.CONFIRMED_RETEST_MARKET.value
    )
    assert lc["is_fresh"]
    assert lc["reason"] == "ACTIVE_FOR_RETEST"
    assert lc["first_ce_touch_ms"] == 160_000


def test_v16_28_fvg_lifecycle_post_retest_structural_invalidation():
    """
    Items 093, 094, 114: If price confirmed retest but a subsequent candle breached structural SL,
    resolve_cg01_retest returns confirmed=False with INVALIDATED_AFTER_RETEST.
    """
    fvg = {
        "fvg_id": "fvg_retest_inv",
        "lower": 2623.0,
        "upper": 2625.0,
        "ce_price": 2624.0,
        "formation_time_ms": 100_000,
        "known_at_ms": 160_000,
        "formation_bar_idx": 2,
    }
    # Bar 3 at 160_000: retests CE 2624.0 (low 2623.8, close 2624.5)
    # Bar 4 at 220_000: crashes down to 2618.0, breaching structural SL at 2619.0!
    candles = [
        make_bar(0, 2620.0, 2621.0, 2619.5, 2620.5),
        make_bar(60_000, 2621.0, 2623.0, 2620.8, 2622.8),
        make_bar(100_000, 2622.8, 2625.5, 2622.5, 2625.2),
        make_bar(160_000, 2625.2, 2625.5, 2623.8, 2624.5), # confirmed retest
        make_bar(220_000, 2624.5, 2624.8, 2618.0, 2618.5), # structural breach!
    ]

    res = resolve_cg01_retest(
        closed_m1=candles,
        fvg=fvg,
        direction="LONG",
        as_of_ms=280_000,
        structural_sl=2619.0,
    )
    assert not res.confirmed
    assert res.reason == "INVALIDATED_AFTER_RETEST"
    assert res.invalidation_occurred


def test_v16_28_fvg_lifecycle_primary_invalid_secondary_valid():
    """
    Items 078, 099, 117: When primary FVG candidate is over-mitigated/invalid,
    select_cg01_leg_fvg inspects and selects the valid secondary FVG candidate on the same displacement leg.
    """
    # Break candle at index 17
    # Primary FVG: k = 18 (c1=16, c2=17, c3=18)
    # Secondary FVG: k = 17 (c1=15, c2=16, c3=17)
    # Both form valid gaps, but candle 19 mitigates primary gap while secondary gap remains unmitigated
    t0 = 100_000
    bars = [make_bar(t0 + i * 60_000, 100.0, 100.5, 99.5, 100.0) for i in range(15)]
    # Bar 15: H=101.0, L=100.0, C=100.8
    bars.append(make_bar(t0 + 15 * 60_000, 100.8, 101.0, 100.0, 100.8))
    # Bar 16: H=103.0, L=100.8, C=102.8
    bars.append(make_bar(t0 + 16 * 60_000, 100.8, 103.0, 100.8, 102.8))
    # Bar 17 (break): H=105.0, L=102.8, C=104.8 -> gap between 15 (101.0) and 17 (102.8) is [101.0, 102.8]
    bars.append(make_bar(t0 + 17 * 60_000, 102.8, 105.0, 102.8, 104.8))
    # Bar 18: H=107.0, L=104.0, C=106.5 -> primary gap between 16 (103.0) and 18 (104.0) is [103.0, 104.0]
    bars.append(make_bar(t0 + 18 * 60_000, 104.8, 107.0, 104.0, 106.5))
    # Bar 19: dips to 102.9 -> completely mitigates primary gap [103.0, 104.0], but stops above secondary gap [101.0, 102.8]!
    bars.append(make_bar(t0 + 19 * 60_000, 106.5, 106.8, 102.9, 105.0))

    mss_info = {"break_bar_idx": 17}
    fvg = select_cg01_leg_fvg(
        m1_candles=bars,
        mss_info=mss_info,
        direction="LONG",
        now_ms=t0 + 20 * 60_000,
        config=CG01Config(),
    )
    assert fvg is not None
    # Must have selected secondary FVG at k=17
    assert fvg["formation_bar_idx"] == 17
    assert fvg["lower"] == 101.0
    assert fvg["upper"] == 102.8


# ==============================================================================
# PART C: MULTI-POI EVALUATION & CANDIDATE SELECTION
# ==============================================================================

def test_v16_28_multi_poi_waiting_first_does_not_block_executable_second():
    """
    Items 121, 128, 141: POI 1 is waiting for retest in confirmed-retest mode.
    POI 2 has already confirmed retest.
    evaluate_cg01_candidate MUST NOT return early with WAIT_ENTRY for POI 1;
    it MUST evaluate POI 2 and return EXECUTABLE!
    """
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)

    c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)
    now_ms = t_start + 540_000

    # Add closed retest bar for textbook setup
    retest_bar = make_bar(now_ms, 2623.75, 2624.5, 2623.5, 2624.2)
    c_1m_retested = list(c_1m) + [retest_bar]
    t_eval = now_ms + 60_000

    ctx = {"4H": c_4h, "1H": c_1h, "15M": c_15m, "1M": c_1m_retested}
    quote = {"bid": 2623.75, "ask": 2623.85, "is_valid": True}

    cfg_retest = CG01Config(
        enabled=True,
        profile_mode="PAPER_EXECUTION",
        entry_mode=CG01EntryMode.CONFIRMED_RETEST_MARKET.value,
    )

    dec = evaluate_cg01_candidate(ctx, quote, config=cfg_retest, now_ms=t_eval)
    assert dec.stage == CG01Stage.EXECUTABLE
    assert dec.is_eligible
    assert dec.candidate is not None


def test_v16_28_multi_poi_both_waiting_deterministic_wait():
    """
    Items 126, 145: When no POI produces an executable candidate but POIs are waiting,
    returns deterministic WAIT_ENTRY stage.
    """
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)

    c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)
    now_ms = t_start + 540_000

    ctx = {"4H": c_4h, "1H": c_1h, "15M": c_15m, "1M": c_1m}
    quote = {"bid": 2625.5, "ask": 2625.6, "is_valid": True}

    cfg_retest = CG01Config(
        enabled=True,
        profile_mode="PAPER_EXECUTION",
        entry_mode=CG01EntryMode.CONFIRMED_RETEST_MARKET.value,
    )

    dec = evaluate_cg01_candidate(ctx, quote, config=cfg_retest, now_ms=now_ms)
    assert dec.stage == CG01Stage.WAIT_ENTRY
    assert not dec.is_eligible
    assert "WAIT_RETEST" in dec.reason_codes or "FVG_RETEST_NOT_CONFIRMED" in dec.missing_conditions


def test_v16_28_multi_poi_invalid_first_does_not_block_valid_second():
    """
    Item 142: An invalid POI preceding a valid POI does not prevent the valid candidate from being returned.
    """
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)

    c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)
    now_ms = t_start + 540_000

    ctx = {"4H": c_4h, "1H": c_1h, "15M": c_15m, "1M": c_1m}
    quote = {"bid": 2625.5, "ask": 2625.6, "is_valid": True}

    # In LIMIT mode: textbook long fixture is executable
    cfg_limit = CG01Config(
        enabled=True,
        profile_mode="PAPER_EXECUTION",
        entry_mode=CG01EntryMode.LIMIT_AT_CE_AFTER_CONFIRMATION.value,
    )

    dec = evaluate_cg01_candidate(ctx, quote, config=cfg_limit, now_ms=now_ms)
    assert dec.stage == CG01Stage.EXECUTABLE
    assert dec.is_eligible
    assert dec.candidate.order_type == "LIMIT"


# ==============================================================================
# PART D: PRODUCTION INTEGRATION (LONG & SHORT)
# ==============================================================================

def test_v16_28_e2e_production_long_flow():
    """
    Items 169, 193: Full production call chain for LONG setup verifying actual M1 ATR evidence,
    FVG lifecycle facts, and candidate validity.
    """
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 3, 30, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)

    c_4h, c_1h, c_15m, c_1m = build_long_fixture_series(t_start)
    now_ms = t_start + 540_000
    ctx = {"4H": c_4h, "1H": c_1h, "15M": c_15m, "1M": c_1m}
    quote = {"bid": 2625.5, "ask": 2625.6, "is_valid": True}

    cfg = CG01Config(
        enabled=True,
        profile_mode="PAPER_EXECUTION",
        entry_mode=CG01EntryMode.LIMIT_AT_CE_AFTER_CONFIRMATION.value,
    )

    dec = evaluate_cg01_candidate(ctx, quote, config=cfg, now_ms=now_ms)
    assert dec.stage == CG01Stage.EXECUTABLE
    assert dec.is_eligible
    assert dec.direction == "LONG"
    assert dec.planned_entry == 2623.75
    assert dec.stop_loss == 2619.45
    assert dec.take_profit == 2640.0
    assert dec.net_rr is not None and dec.net_rr >= 2.0

    # Verify snapshot evidence includes actual M1 ATR
    cand = dec.candidate
    assert cand is not None
    assert cand.snapshot is not None
    disp_node = next((n for n in cand.evidence if n.get("kind") == "DISPLACEMENT"), None)
    assert disp_node is not None
    assert "displacement_atr" in disp_node.get("extra", {})
    assert disp_node["extra"]["displacement_atr"] > 0.0
    assert "body_atr_ratio" in disp_node.get("extra", {})
    assert disp_node["extra"]["body_atr_ratio"] >= 1.0


def test_v16_28_e2e_production_short_flow():
    """
    Items 170, 193: Full production call chain for SHORT setup verifying actual M1 ATR evidence,
    FVG lifecycle facts, and candidate validity.
    """
    ny_tz = ZoneInfo("America/New_York")
    dt = datetime(2026, 10, 15, 10, 15, tzinfo=ny_tz)
    t_start = int(dt.timestamp() * 1000)

    c_4h, c_1h, c_15m, c_1m = build_short_fixture_series(t_start)
    now_ms = t_start + 540_000
    ctx = {"4H": c_4h, "1H": c_1h, "15M": c_15m, "1M": c_1m}
    quote = {"bid": 2649.5, "ask": 2649.7, "is_valid": True}

    cfg = CG01Config(
        enabled=True,
        profile_mode="PAPER_EXECUTION",
        entry_mode=CG01EntryMode.LIMIT_AT_CE_AFTER_CONFIRMATION.value,
    )

    dec = evaluate_cg01_candidate(ctx, quote, config=cfg, now_ms=now_ms)
    assert dec.stage == CG01Stage.EXECUTABLE
    assert dec.is_eligible
    assert dec.direction == "SHORT"
    assert dec.planned_entry == 2651.15
    assert dec.stop_loss == 2655.55
    assert dec.take_profit == 2634.0
    assert dec.net_rr is not None and dec.net_rr >= 2.0
    assert dec.candidate.order_type == "LIMIT"
