"""
backend/tests/test_v16_14_htf_pullback_correctness.py
V16.14 HTF Correctness Regression Tests:
- Rule 021: Bullish internal pullback fallthrough when BOS is not confirmed.
- Rule 022: Bearish internal pullback fallthrough when BOS is not confirmed.
- Rule 023: State priority: Protected break > Sweep rejection > BOS > Pullback.
- Rule 024 & 171: Strict closed candle filter in build_cg01_context (is_closed=True but future close rejected).
- Rule 026 & 027: Collector accepts custom config and synchronizes with evaluator.
"""

import pytest
from services.cg01_types import CG01Config
from services.cg01_engine import build_cg01_context
from services.collector_service import collector_service
from services.htf_structure_service import (
    HTFSettings,
    HTFState,
    evaluate_htf_structure,
    step_htf_state,
)
from tests.test_v16_11_htf_structure import CandleMock


# =========================================================================
# 1. Rule 021: Bullish Internal Pullback Fallthrough
# =========================================================================
def test_bullish_internal_pullback_fallthrough_when_bos_unconfirmed():
    """
    Rule 021: When st.reference_high exists and bar does not confirm BOS,
    step_htf_state must fall through to evaluate internal pullback.
    """
    settings = HTFSettings(tick_size=0.1, break_buffer_ticks=1)

    prot_low = {"id": "sw_l_1", "price": 2500.0, "occurred_at_ms": 1000, "known_at_ms": 2000}
    higher_low = {"id": "sw_l_2", "price": 2520.0, "occurred_at_ms": 3000, "known_at_ms": 4000}
    ref_high = {"id": "sw_h_1", "price": 2560.0, "occurred_at_ms": 2000, "known_at_ms": 3000}

    state = HTFState(
        bias="BULLISH",
        last_confirmed_bias="BULLISH",
        phase="ESTABLISHED",
        protected_low=prot_low,
        reference_high=ref_high,
        reference_low=higher_low,
    )

    known_h = [ref_high]
    known_l = [prot_low, higher_low]

    # Bar pulls back below higher_low (2520.0 - 0.1 = 2519.9) to 2515.0, but above prot_low (2500.0)
    pullback_bar = CandleMock(
        open_time=5000,
        o=2525.0,
        h=2530.0,
        l=2514.0,
        c=2515.0,
        is_closed=True,
        tf="1H",
    )

    res = step_htf_state(state, pullback_bar, known_h, known_l, settings, "1H", "XAUUSDT")

    # In V16.13 baseline, elif st.reference_high prevented falling through to pullback check.
    # In V16.14, this must transition phase to PULLBACK.
    assert res.state.phase == "PULLBACK", f"Expected PULLBACK, got {res.state.phase}"
    assert res.state.bias == "BULLISH"
    assert res.new_event is None


# =========================================================================
# 2. Rule 022: Bearish Internal Pullback Fallthrough
# =========================================================================
def test_bearish_internal_pullback_fallthrough_when_bos_unconfirmed():
    """
    Rule 022: Symmetrically for BEARISH, when st.reference_low exists and bar does not confirm BOS,
    step_htf_state must fall through to evaluate internal pullback.
    """
    settings = HTFSettings(tick_size=0.1, break_buffer_ticks=1)

    prot_high = {"id": "sw_h_1", "price": 2600.0, "occurred_at_ms": 1000, "known_at_ms": 2000}
    lower_high = {"id": "sw_h_2", "price": 2580.0, "occurred_at_ms": 3000, "known_at_ms": 4000}
    ref_low = {"id": "sw_l_1", "price": 2540.0, "occurred_at_ms": 2000, "known_at_ms": 3000}

    state = HTFState(
        bias="BEARISH",
        last_confirmed_bias="BEARISH",
        phase="ESTABLISHED",
        protected_high=prot_high,
        reference_high=lower_high,
        reference_low=ref_low,
    )

    known_h = [prot_high, lower_high]
    known_l = [ref_low]

    # Bar pulls back above lower_high (2580.0 + 0.1 = 2580.1) to 2585.0, but below prot_high (2600.0)
    pullback_bar = CandleMock(
        open_time=5000,
        o=2575.0,
        h=2586.0,
        l=2574.0,
        c=2585.0,
        is_closed=True,
        tf="1H",
    )

    res = step_htf_state(state, pullback_bar, known_h, known_l, settings, "1H", "XAUUSDT")

    assert res.state.phase == "PULLBACK", f"Expected PULLBACK, got {res.state.phase}"
    assert res.state.bias == "BEARISH"
    assert res.new_event is None


# =========================================================================
# 3. Rule 023: State Priority (Protected Break > Pullback)
# =========================================================================
def test_protected_break_takes_strict_priority_over_pullback():
    """
    Rule 023: A candle that closes below protected low MUST trigger TRANSITION
    via PROTECTED_LOW_BREAK, never mislabeled as PULLBACK.
    """
    settings = HTFSettings(tick_size=0.1, break_buffer_ticks=1)

    prot_low = {"id": "sw_l_1", "price": 2500.0, "occurred_at_ms": 1000, "known_at_ms": 2000}
    higher_low = {"id": "sw_l_2", "price": 2520.0, "occurred_at_ms": 3000, "known_at_ms": 4000}
    ref_high = {"id": "sw_h_1", "price": 2560.0, "occurred_at_ms": 2000, "known_at_ms": 3000}

    state = HTFState(
        bias="BULLISH",
        last_confirmed_bias="BULLISH",
        phase="ESTABLISHED",
        protected_low=prot_low,
        reference_high=ref_high,
        reference_low=higher_low,
    )

    known_h = [ref_high]
    known_l = [prot_low, higher_low]

    # Bar closes below protected low (2500.0 - 0.1 = 2499.9) at 2495.0
    breaking_bar = CandleMock(
        open_time=5000,
        o=2510.0,
        h=2512.0,
        l=2490.0,
        c=2495.0,
        is_closed=True,
        tf="1H",
    )

    res = step_htf_state(state, breaking_bar, known_h, known_l, settings, "1H", "XAUUSDT")

    assert res.state.phase == "TRANSITION"
    assert res.state.bias == "UNKNOWN"
    assert res.new_event is not None
    assert res.new_event.event_type == "PROTECTED_LOW_BREAK"


# =========================================================================
# 4. Rule 024 & 171: Strict Closed Candle Filter in build_cg01_context
# =========================================================================
def test_future_candle_with_explicit_is_closed_true_is_strictly_rejected():
    """
    Rule 024, 171: Candles marked is_closed=True but having close_ms > now_ms
    must be strictly filtered out by build_cg01_context.
    """
    base_t = 1700000000000
    now_ms = base_t + 10 * 3600000  # 10 hours from base

    # 15 candles generated, but bars 11-15 close AFTER now_ms
    c_1h = []
    for i in range(15):
        t = base_t + i * 3600000
        c_1h.append({
            "open_time": t,
            "open": 2500.0 + i,
            "high": 2510.0 + i,
            "low": 2495.0 + i,
            "close": 2505.0 + i,
            "close_time": t + 3600000,
            "is_closed": True,  # Explicitly marked closed
        })

    # When evaluated at now_ms (10 hours), only 10 candles are causally closed
    ctx = {
        "4H": c_1h,
        "1H": c_1h,
        "15M": c_1h,
        "1M": c_1h,
    }

    cfg = CG01Config(htf_min_bars=12)
    res = build_cg01_context(ctx, now_ms=now_ms, config=cfg)

    # Because only 10 bars closed <= now_ms (< 12 min_bars), status must be CONTEXT_WARMUP
    assert res["status"] == "CONTEXT_WARMUP"
    assert res["is_warmup"] is True


# =========================================================================
# 5. Rule 026 & 027: Collector Service Custom Config Synchronization
# =========================================================================
def test_collector_service_respects_custom_config_and_synchronizes():
    """
    Rule 026, 027: Collector accepts explicit custom config and sets effective settings.
    """
    custom_cfg = CG01Config(htf_min_bars=50, htf_break_buffer_ticks=3, tick_size=0.5)
    collector_service.set_effective_config(custom_cfg)

    assert collector_service._effective_config is not None
    assert collector_service._effective_config.htf_min_bars == 50
    assert collector_service._effective_config.htf_break_buffer_ticks == 3
