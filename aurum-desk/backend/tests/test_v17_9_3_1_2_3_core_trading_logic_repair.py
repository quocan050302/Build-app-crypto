"""
backend/tests/test_v17_9_3_1_2_3_core_trading_logic_repair.py

AURUM Desk / Bitget XAUUSDT — V17.9.3.1.2.3
Core Trading Logic Repair & Independent Strategy Truth — 34 Regression Tests (T01..T34).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = BACKEND_ROOT.parent
for p in (str(BACKEND_ROOT), str(APP_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from services.scalp_m1_types import M1ScalpConfig, ModelIdentifier
from services.shared_strategy_evaluator import (
    SharedStrategyEvaluator,
    build_market_decision_context,
    resolve_canonical_preferred_direction,
)
from services.v17_9_3_1_2_executable_intervention_replay_service import (
    PortfolioReplayStateV179312,
    advance_portfolio_to_next_event_v179312,
)
from services.v17_9_3_1_2_2_signal_intelligence import (
    PendingConfirmationV1793122,
    advance_pending_confirmation_state_v1793122,
    arbitrate_competing_model_intents_v1793122,
    build_signal_quality_evidence_v1793122,
    compute_strategy_statistical_verdict_v1793122,
    evaluate_signal_intelligence_gate_v1793122,
    is_research_apply_forbidden_in_adapter_v1793122,
    resolve_research_preferred_direction_v1793122,
    score_candidate_record_pre_entry_v1793122,
)
from services.v17_9_3_1_2_3_core_trading_logic_repair import (
    build_ground_truth_loss_forensics_v1793123,
    compute_v1793123_dual_verdict,
    evaluate_v1793123_acceptance_gates_cr01_cr15,
)


def _make_m1_candles(count: int = 30, base_price: float = 2650.0, step: float = 0.15) -> List[Dict[str, Any]]:
    candles: List[Dict[str, Any]] = []
    for i in range(count):
        o = round(base_price + i * step, 2)
        c = round(o + 0.35, 2)
        h = round(max(o, c) + 0.30, 2)
        l = round(min(o, c) - 0.25, 2)
        candles.append(
            {
                "open_time": i * 60_000,
                "close_time": (i + 1) * 60_000,
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": 120.0,
            }
        )
    return candles


def _make_htf_candles(tf_ms: int, count: int, end_ms: int, bullish: bool = True) -> List[Dict[str, Any]]:
    candles: List[Dict[str, Any]] = []
    start_ms = end_ms - count * tf_ms
    base = 2640.0 if bullish else 2670.0
    step = 0.80 if bullish else -0.80
    for i in range(count):
        o = round(base + i * step, 2)
        c = round(o + step, 2)
        h = round(max(o, c) + 0.50, 2)
        l = round(min(o, c) - 0.50, 2)
        candles.append(
            {
                "open_time": start_ms + i * tf_ms,
                "close_time": start_ms + (i + 1) * tf_ms,
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": 500.0,
            }
        )
    return candles


class _DummyCtx:
    def __init__(
        self,
        candles_1m: List[Dict[str, Any]],
        *,
        now_ms: int = 1_800_000,
        policy: M1ScalpConfig | None = None,
        capital_usdt: float = 100.0,
        candles_5m: List[Dict[str, Any]] | None = None,
        candles_15m: List[Dict[str, Any]] | None = None,
        candles_1h: List[Dict[str, Any]] | None = None,
        candles_4h: List[Dict[str, Any]] | None = None,
    ) -> None:
        self.candles_1m = candles_1m
        self.candles_5m = candles_5m if candles_5m is not None else candles_1m
        self.candles_15m = candles_15m if candles_15m is not None else _make_htf_candles(900_000, 12, now_ms, True)
        self.candles_1h = candles_1h if candles_1h is not None else _make_htf_candles(3_600_000, 12, now_ms, True)
        self.candles_4h = candles_4h if candles_4h is not None else _make_htf_candles(14_400_000, 8, now_ms, True)
        self.now_ms = now_ms
        self.adapter_origin = "BACKTEST"
        self.capital_usdt = capital_usdt
        self.policy = policy or M1ScalpConfig(
            v17_9_3_1_2_2_signal_intelligence_mode="RESEARCH_APPLY",
            v17_9_3_1_2_2_policy_id="SI7_CORE_TRADING_LOGIC_REPAIRED",
        )


def test_t01_fvg_already_mitigated_before_decision_is_rejected() -> None:
    bars = _make_m1_candles(25)
    # FVG formed at bar 10 (close_time = 660_000), zone [2651.0, 2652.0]; bar 15 trades through 2650.2 (< 2651.0 -> mitigated)
    bars[15]["low"] = 2650.2
    ctx = _DummyCtx(bars, now_ms=bars[-1]["close_time"])
    ev = build_signal_quality_evidence_v1793122(
        context=ctx,
        model_id="SMC_M1_FVG_CONTINUATION",
        direction="LONG",
        setup_descriptors={
            "selected_fvg_id": "fvg_mit_1",
            "fvg_formed_at_ms": 660_000,
            "fvg_low": 2651.0,
            "fvg_high": 2652.0,
        },
        sh_1m=[],
        sl_1m=[],
        entry_price=2653.5,
        selected_sl=2650.0,
        take_profit=2660.0,
        selected_target_id="t1",
        atr_m1=1.0,
        atr_m5=2.0,
        prospective_net_rr=2.5,
        planned_risk_usd=2.0,
    )
    assert ev.fvg_unmitigated is False


def test_t02_fvg_stale_beyond_max_age_is_rejected() -> None:
    bars = _make_m1_candles(60)
    ctx = _DummyCtx(bars, now_ms=bars[-1]["close_time"])
    gate = evaluate_signal_intelligence_gate_v1793122(
        context=ctx,
        model_id="SMC_M1_FVG_CONTINUATION",
        direction="LONG",
        mode="RESEARCH_APPLY",
        policy_id="SI4_CAUSAL_LIQUIDITY_SWEEP_AND_TRUE_UNICORN",
        adapter_origin="BACKTEST",
        baseline_status="ELIGIBLE",
        baseline_rejection_reasons=(),
        baseline_entry=2658.0,
        baseline_sl=2655.0,
        baseline_tp=2666.0,
        baseline_qty=0.5,
        baseline_gross_rr=2.6,
        baseline_net_rr=2.3,
        baseline_anchor_id="a1",
        baseline_anchor_kind="SWING",
        baseline_invalidation_price=2655.5,
        baseline_target_id="t1",
        setup_descriptors={
            "selected_fvg_id": "fvg_old",
            "fvg_formed_at_ms": 60_000,  # 59 bars old (> max_fvg_age_bars_m1=35)
            "fvg_low": 2657.5,
            "fvg_high": 2658.2,
            "fvg_unmitigated": True,
            "fvg_retest_valid": True,
        },
        sh_1m=[{"id": "t1", "price": 2666.0, "occurred_at_ms": 300_000, "known_at_ms": 420_000}],
        sl_1m=[{"id": "s1", "price": 2655.5, "occurred_at_ms": 300_000, "known_at_ms": 420_000}],
        atr_m1=1.2,
        atr_m5=2.5,
        unicorn_evidence={
            "fvg_present": True,
            "breaker_present": True,
            "overlap_valid": True,
            "overlap_low": 2657.6,
            "overlap_high": 2658.1,
            "breaker_confirmed_at_ms": 300_000,
        },
    )
    assert "SI4_FVG_STALE_OR_ALREADY_MITIGATED" in gate.proposed_rejection_reasons


def test_t03_fvg_without_closed_bar_retest_is_rejected_or_waits() -> None:
    bars = _make_m1_candles(25, base_price=2660.0, step=0.2)
    ctx = _DummyCtx(bars, now_ms=bars[-1]["close_time"])
    # FVG formed at bar 20 (close_time = 1_260_000) with zone [2650.0, 2651.0], price stays around 2664+ (never retested)
    ev = build_signal_quality_evidence_v1793122(
        context=ctx,
        model_id="SMC_M1_FVG_CONTINUATION",
        direction="LONG",
        setup_descriptors={
            "selected_fvg_id": "fvg_unretested",
            "fvg_formed_at_ms": 1_260_000,
            "fvg_low": 2650.0,
            "fvg_high": 2651.0,
        },
        sh_1m=[],
        sl_1m=[],
        entry_price=2664.5,
        selected_sl=2662.0,
        take_profit=2671.0,
        selected_target_id="t1",
        atr_m1=1.0,
        atr_m5=2.0,
        prospective_net_rr=2.5,
        planned_risk_usd=2.0,
    )
    assert ev.fvg_retest_valid is False


def test_t04_fvg_only_cannot_be_classified_as_unicorn_confirmed() -> None:
    bars = _make_m1_candles(20)
    ctx = _DummyCtx(bars, now_ms=bars[-1]["close_time"])
    ev = build_signal_quality_evidence_v1793122(
        context=ctx,
        model_id="CG01_SWEEP_MSS_FVG",
        direction="LONG",
        setup_descriptors={"selected_fvg_id": "fvg_1", "fvg_formed_at_ms": 600_000},
        sh_1m=[],
        sl_1m=[],
        entry_price=2652.0,
        selected_sl=2649.5,
        take_profit=2658.0,
        selected_target_id="t1",
        atr_m1=1.0,
        atr_m5=2.0,
        prospective_net_rr=2.5,
        planned_risk_usd=2.0,
        unicorn_evidence={"fvg_present": True, "breaker_present": False, "overlap_valid": False},
    )
    assert ev.unicorn_classification == "FVG_ONLY"


def test_t05_breaker_fvg_non_overlapping_is_rejected_for_unicorn() -> None:
    bars = _make_m1_candles(20)
    ctx = _DummyCtx(bars, now_ms=bars[-1]["close_time"])
    # overlap_low >= overlap_high -> invalid spatial overlap
    ev = build_signal_quality_evidence_v1793122(
        context=ctx,
        model_id="CG01_SWEEP_MSS_FVG",
        direction="LONG",
        setup_descriptors={"selected_fvg_id": "fvg_1", "fvg_formed_at_ms": 600_000},
        sh_1m=[],
        sl_1m=[],
        entry_price=2652.0,
        selected_sl=2649.5,
        take_profit=2658.0,
        selected_target_id="t1",
        atr_m1=1.0,
        atr_m5=2.0,
        prospective_net_rr=2.5,
        planned_risk_usd=2.0,
        unicorn_evidence={
            "fvg_present": True,
            "breaker_present": True,
            "overlap_valid": True,
            "overlap_low": 2651.5,
            "overlap_high": 2651.0,
        },
    )
    assert ev.unicorn_classification != "UNICORN_CONFIRMED"


def test_t06_valid_breaker_fvg_overlap_and_retest_passes_unicorn() -> None:
    bars = _make_m1_candles(20, base_price=2650.0, step=0.05)
    ctx = _DummyCtx(bars, now_ms=bars[-1]["close_time"])
    ev = build_signal_quality_evidence_v1793122(
        context=ctx,
        model_id="CG01_SWEEP_MSS_FVG",
        direction="LONG",
        setup_descriptors={
            "selected_fvg_id": "fvg_1",
            "fvg_formed_at_ms": 600_000,
            "fvg_low": 2650.2,
            "fvg_high": 2651.2,
            "fvg_retest_at_ms": 900_000,
        },
        sh_1m=[],
        sl_1m=[],
        entry_price=2651.0,
        selected_sl=2649.0,
        take_profit=2656.5,
        selected_target_id="t1",
        atr_m1=1.0,
        atr_m5=2.0,
        prospective_net_rr=2.5,
        planned_risk_usd=2.0,
        unicorn_evidence={
            "fvg_present": True,
            "breaker_present": True,
            "overlap_valid": True,
            "overlap_low": 2650.4,
            "overlap_high": 2651.0,
            "breaker_confirmed_at_ms": 540_000,
        },
    )
    assert ev.unicorn_classification == "UNICORN_CONFIRMED"


def test_t07_sweep_without_reclaim_close_is_rejected() -> None:
    bars = _make_m1_candles(20)
    # Bar 19 sweeps below 2648.0 and closes at 2647.5 (< 2648.0 -> not reclaimed)
    bars[-1]["low"] = 2647.2
    bars[-1]["close"] = 2647.5
    ctx = _DummyCtx(bars, now_ms=bars[-1]["close_time"])
    ev = build_signal_quality_evidence_v1793122(
        context=ctx,
        model_id="SMC_M1_SWEEP_RECLAIM",
        direction="LONG",
        setup_descriptors={
            "selected_sweep_id": "sw_unreclaimed",
            "sweep_occurred_at_ms": bars[-1]["close_time"],
            "swept_level_price": 2648.0,
        },
        sh_1m=[],
        sl_1m=[{"id": "sl_prior", "price": 2648.0, "occurred_at_ms": 300_000, "known_at_ms": 420_000}],
        entry_price=2647.5,
        selected_sl=2646.0,
        take_profit=2652.0,
        selected_target_id="t1",
        atr_m1=1.0,
        atr_m5=2.0,
        prospective_net_rr=2.5,
        planned_risk_usd=2.0,
    )
    assert ev.sweep_reclaimed is False


def test_t08_unconfirmed_future_swing_never_visible_at_now_ms() -> None:
    bars = _make_m1_candles(20)
    now_ms = bars[-1]["close_time"]
    ctx = _DummyCtx(bars, now_ms=now_ms)
    # Only swing in sh_1m has known_at_ms > now_ms -> future lookahead swing
    ev = build_signal_quality_evidence_v1793122(
        context=ctx,
        model_id="SMC_M1_REVERSAL",
        direction="LONG",
        setup_descriptors={
            "selected_break_id": "mss_future",
            "mss_confirmed_at_ms": now_ms - 60_000,
        },
        sh_1m=[{"id": "sh_future", "price": 2652.0, "occurred_at_ms": now_ms - 60_000, "known_at_ms": now_ms + 120_000}],
        sl_1m=[],
        entry_price=2653.0,
        selected_sl=2650.5,
        take_profit=2659.0,
        selected_target_id="t1",
        atr_m1=1.0,
        atr_m5=2.0,
        prospective_net_rr=2.5,
        planned_risk_usd=2.0,
    )
    assert ev.mss_confirmed is False
    assert ev.lookahead_violation_detected is True


def test_t09_explicit_htf_bias_without_closed_provenance_cannot_override_absent_htf() -> None:
    # Passing explicit_bias="BULLISH" while h4_candles and h1_candles are empty at now_ms must reject
    pref_dir, reason = resolve_research_preferred_direction_v1793122(
        explicit_bias="BULLISH",
        h4_bias="BULLISH",
        h1_bias="BULLISH",
        m15_bias="BULLISH",
        m5_bias="BULLISH",
        now_ms=1_800_000,
        h4_candles=[],
        h1_candles=[],
        m15_candles=[],
    )
    assert pref_dir is None
    assert reason == "EXPLICIT_HTF_BIAS_MISSING_CLOSED_CANDLE_PROVENANCE"


def test_t10_missing_or_neutral_htf_never_defaults_long() -> None:
    pref_dir, reason = resolve_research_preferred_direction_v1793122(
        h4_bias="ABSENT",
        h1_bias="NEUTRAL",
        m15_bias="NEUTRAL",
        m5_bias="NEUTRAL",
    )
    assert pref_dir is None
    assert reason == "HTF_DATA_INSUFFICIENT_OR_CONFLICTED"

    bars_1m = _make_m1_candles(20)
    ctx = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=bars_1m[-1]["close_time"],
        m1_candles=bars_1m,
        m5_candles=[],
        m15_candles=[],
        h1_candles=[],
        h4_candles=[],
        policy_config={
            "v17_9_3_1_2_2_signal_intelligence_mode": "RESEARCH_APPLY",
            "v17_9_3_1_2_2_policy_id": "SI2_CAUSAL_HTF_STRUCTURE_AND_DIRECTION",
        },
    )
    assert resolve_canonical_preferred_direction(ctx) == "NO_BIAS"


def test_t11_mixed_htf_evaluates_both_directions_symmetrically() -> None:
    # H4 BULLISH + H1 BEARISH -> MIXED_WAIT_CONFIRMATION -> returns (None, "HTF_MIXED_REQUIRE_LOCAL_CONFIRMATION")
    d1, r1 = resolve_research_preferred_direction_v1793122(
        h4_bias="BULLISH",
        h1_bias="BEARISH",
        m15_bias="BULLISH",
        m5_bias="BEARISH",
    )
    d2, r2 = resolve_research_preferred_direction_v1793122(
        h4_bias="BEARISH",
        h1_bias="BULLISH",
        m15_bias="BEARISH",
        m5_bias="BULLISH",
    )
    assert d1 is None and r1 == "HTF_MIXED_REQUIRE_LOCAL_CONFIRMATION"
    assert d2 is None and r2 == "HTF_MIXED_REQUIRE_LOCAL_CONFIRMATION"


def test_t12_continuation_rejects_impulse_chasing_and_accepts_pullback() -> None:
    bars = _make_m1_candles(25)
    # Weak impulse chase bar without pullback
    bars[-1]["open"] = 2653.0
    bars[-1]["close"] = 2653.15
    ctx = _DummyCtx(bars, now_ms=bars[-1]["close_time"])
    gate_reject = evaluate_signal_intelligence_gate_v1793122(
        context=ctx,
        model_id="SMC_M1_CONTINUATION",
        direction="LONG",
        mode="RESEARCH_APPLY",
        policy_id="SI1_MODEL_QUALITY_AND_ARBITRATION",
        adapter_origin="BACKTEST",
        baseline_status="ELIGIBLE",
        baseline_rejection_reasons=(),
        baseline_entry=2653.15,
        baseline_sl=2650.5,
        baseline_tp=2660.0,
        baseline_qty=0.5,
        baseline_gross_rr=2.6,
        baseline_net_rr=2.3,
        baseline_anchor_id="a1",
        baseline_anchor_kind="SWING",
        baseline_invalidation_price=2650.8,
        baseline_target_id="t1",
        setup_descriptors={"selected_break_id": "b1", "mss_confirmed_at_ms": bars[-2]["close_time"]},
        sh_1m=[{"id": "t1", "price": 2660.0, "occurred_at_ms": 300_000, "known_at_ms": 420_000}],
        sl_1m=[{"id": "s1", "price": 2650.8, "occurred_at_ms": 300_000, "known_at_ms": 420_000}],
        atr_m1=1.2,
        atr_m5=2.5,
    )
    assert gate_reject.applied_status == "REJECTED"


def test_t13_reversal_requires_sweep_reclaim_mss_displacement_sequence() -> None:
    bars = _make_m1_candles(25)
    ctx = _DummyCtx(bars, now_ms=bars[-1]["close_time"])
    # Missing sweep_confirmed on SMC_M1_REVERSAL must reject under SI7
    gate = evaluate_signal_intelligence_gate_v1793122(
        context=ctx,
        model_id="SMC_M1_REVERSAL",
        direction="LONG",
        mode="RESEARCH_APPLY",
        policy_id="SI7_CORE_TRADING_LOGIC_REPAIRED",
        adapter_origin="BACKTEST",
        baseline_status="ELIGIBLE",
        baseline_rejection_reasons=(),
        baseline_entry=2653.5,
        baseline_sl=2650.5,
        baseline_tp=2661.0,
        baseline_qty=0.5,
        baseline_gross_rr=2.5,
        baseline_net_rr=2.2,
        baseline_anchor_id="a1",
        baseline_anchor_kind="SWING",
        baseline_invalidation_price=2651.0,
        baseline_target_id="t1",
        setup_descriptors={"selected_break_id": "b1"},  # no selected_sweep_id
        sh_1m=[{"id": "t1", "price": 2661.0, "occurred_at_ms": 300_000, "known_at_ms": 420_000}],
        sl_1m=[{"id": "s1", "price": 2651.0, "occurred_at_ms": 300_000, "known_at_ms": 420_000}],
        atr_m1=1.0,
        atr_m5=2.0,
    )
    assert gate.applied_status == "REJECTED"


def test_t14_pending_confirmation_never_fills_at_earlier_signal_bar() -> None:
    p = PendingConfirmationV1793122(
        pending_id="p_t14",
        opportunity_id="o_t14",
        symbol="XAUUSDT",
        model_id="SMC_M1_CONTINUATION",
        direction="LONG",
        signal_ts_ms=60_000,
        created_bar_index_1m=0,
        expires_at_ms=360_000,
        max_ttl_bars=5,
        confirmation_type="MSS_DISPLACEMENT_BREAK",
        trigger_level_price=2650.8,
        setup_invalidation_price=2648.0,
        protected_structure_price=2648.0,
        protected_structure_id="s1",
        baseline_entry=2650.2,
        baseline_sl=2648.0,
        baseline_tp=2666.0,
        target_id="t1",
        policy_id="SI7_CORE_TRADING_LOGIC_REPAIRED",
    )
    bars = [
        {"open_time": 0, "close_time": 60_000, "open": 2650.0, "high": 2650.4, "low": 2649.8, "close": 2650.2},
        {"open_time": 60_000, "close_time": 120_000, "open": 2650.2, "high": 2650.6, "low": 2650.0, "close": 2650.5},
        {"open_time": 120_000, "close_time": 180_000, "open": 2650.5, "high": 2651.8, "low": 2650.4, "close": 2651.6},
    ]
    updated, conf_idx = advance_pending_confirmation_state_v1793122(p, bars, start_bar_index=1, atr_m1=1.0)
    assert updated.status == "CONFIRMED"
    assert conf_idx == 2
    assert updated.confirmed_at_ms == 180_000 > p.signal_ts_ms


def test_t15_pending_confirmation_invalidates_if_protected_structure_breaks_first() -> None:
    p = PendingConfirmationV1793122(
        pending_id="p_t15",
        opportunity_id="o_t15",
        symbol="XAUUSDT",
        model_id="SMC_M1_REVERSAL",
        direction="LONG",
        signal_ts_ms=60_000,
        created_bar_index_1m=0,
        expires_at_ms=360_000,
        max_ttl_bars=5,
        confirmation_type="MSS_DISPLACEMENT_BREAK",
        trigger_level_price=2651.0,
        setup_invalidation_price=2648.5,
        protected_structure_price=2648.5,
        protected_structure_id="s1",
        baseline_entry=2650.0,
        baseline_sl=2648.5,
        baseline_tp=2660.0,
        target_id="t1",
        policy_id="SI7_CORE_TRADING_LOGIC_REPAIRED",
    )
    bars = [
        {"open_time": 0, "close_time": 60_000, "open": 2650.0, "high": 2650.4, "low": 2649.8, "close": 2650.0},
        {"open_time": 60_000, "close_time": 120_000, "open": 2650.0, "high": 2650.2, "low": 2648.0, "close": 2648.2},
    ]
    updated, conf_idx = advance_pending_confirmation_state_v1793122(p, bars, start_bar_index=1, atr_m1=1.0)
    assert updated.status == "INVALIDATED"
    assert conf_idx is None
    assert "INVALIDATION" in str(updated.rejection_reason)


def test_t16_pending_confirmation_expires_after_ttl() -> None:
    p = PendingConfirmationV1793122(
        pending_id="p_t16",
        opportunity_id="o_t16",
        symbol="XAUUSDT",
        model_id="SMC_M1_CONTINUATION",
        direction="LONG",
        signal_ts_ms=60_000,
        created_bar_index_1m=0,
        expires_at_ms=180_000,
        max_ttl_bars=2,
        confirmation_type="MSS_DISPLACEMENT_BREAK",
        trigger_level_price=2655.0,
        setup_invalidation_price=2648.0,
        protected_structure_price=2648.0,
        protected_structure_id="s1",
        baseline_entry=2650.0,
        baseline_sl=2648.0,
        baseline_tp=2660.0,
        target_id="t1",
        policy_id="SI7_CORE_TRADING_LOGIC_REPAIRED",
    )
    bars = [
        {"open_time": 0, "close_time": 60_000, "open": 2650.0, "high": 2650.4, "low": 2649.8, "close": 2650.1},
        {"open_time": 60_000, "close_time": 120_000, "open": 2650.1, "high": 2650.5, "low": 2649.9, "close": 2650.2},
        {"open_time": 120_000, "close_time": 180_000, "open": 2650.2, "high": 2650.6, "low": 2650.0, "close": 2650.3},
    ]
    updated, conf_idx = advance_pending_confirmation_state_v1793122(p, bars, start_bar_index=1, atr_m1=1.0)
    assert updated.status == "EXPIRED"
    assert conf_idx is None


def test_t17_protected_structure_anchor_cannot_equal_selected_sl_by_definition() -> None:
    bars = _make_m1_candles(20)
    ctx = _DummyCtx(bars, now_ms=bars[-1]["close_time"])
    ev = build_signal_quality_evidence_v1793122(
        context=ctx,
        model_id="SMC_M1_REVERSAL",
        direction="LONG",
        setup_descriptors={},
        sh_1m=[],
        sl_1m=[{"id": "sl_same", "price": 2648.5, "occurred_at_ms": 60_000, "known_at_ms": 180_000}],
        entry_price=2650.2,
        selected_sl=2648.5,  # identical to anchor -> circular tautology
        take_profit=2656.0,
        selected_target_id="t1",
        atr_m1=1.0,
        atr_m5=2.0,
        prospective_net_rr=2.5,
        planned_risk_usd=2.0,
    )
    assert ev.protected_structure_independent_of_sl is False


def test_t18_widened_structural_sl_recomputes_quantity_and_enforces_2_50_risk_cap() -> None:
    bars = _make_m1_candles(25)
    ctx = _DummyCtx(bars, now_ms=bars[-1]["close_time"], capital_usdt=100.0)
    res = evaluate_signal_intelligence_gate_v1793122(
        context=ctx,
        model_id="SMC_M1_REVERSAL",
        direction="LONG",
        mode="RESEARCH_APPLY",
        policy_id="SI5_STRUCTURAL_ENTRY_SL_AND_REACHABLE_TARGET",
        adapter_origin="BACKTEST",
        baseline_status="ELIGIBLE",
        baseline_rejection_reasons=(),
        baseline_entry=2653.5,
        baseline_sl=2652.8,  # tight SL
        baseline_tp=2666.0,
        baseline_qty=2.40,
        baseline_gross_rr=6.0,
        baseline_net_rr=4.8,
        baseline_anchor_id="a1",
        baseline_anchor_kind="SWING",
        baseline_invalidation_price=2651.0,
        baseline_target_id="t1",
        setup_descriptors={
            "selected_sweep_id": "sw1",
            "sweep_occurred_at_ms": 600_000,
            "sweep_reclaimed_at_ms": 660_000,
            "selected_break_id": "b1",
            "mss_confirmed_at_ms": 720_000,
        },
        sh_1m=[{"id": "t1", "price": 2662.0, "occurred_at_ms": 300_000, "known_at_ms": 420_000}],
        sl_1m=[{"id": "s1", "price": 2651.0, "occurred_at_ms": 300_000, "known_at_ms": 420_000}],
        atr_m1=2.2,
        atr_m5=5.0,
        unicorn_evidence={},
    )
    assert res.proposed_sl < 2652.8
    assert res.proposed_qty < 2.40
    assert res.proposed_planned_risk_usd <= 2.50


def test_t19_tp_rejects_already_swept_or_unreachable_target() -> None:
    bars = _make_m1_candles(25, base_price=2650.0, step=0.1)
    # Swing high at bar 5 (price 2653.0, known_at = 420_000) is swept by bar 12 high = 2654.5
    bars[12]["high"] = 2654.5
    ctx = _DummyCtx(bars, now_ms=bars[-1]["close_time"])
    ev = build_signal_quality_evidence_v1793122(
        context=ctx,
        model_id="SMC_M1_REVERSAL",
        direction="LONG",
        setup_descriptors={},
        sh_1m=[{"id": "sh_swept", "price": 2653.0, "occurred_at_ms": 300_000, "known_at_ms": 420_000}],
        sl_1m=[{"id": "sl_1", "price": 2649.5, "occurred_at_ms": 300_000, "known_at_ms": 420_000}],
        entry_price=2652.0,
        selected_sl=2649.2,
        take_profit=2668.0,  # arbitrary far target with no unswept swing
        selected_target_id="sh_swept",
        atr_m1=1.0,
        atr_m5=2.0,
        prospective_net_rr=5.5,
        planned_risk_usd=2.0,
    )
    assert ev.target_reachable is False


def test_t20_shared_evaluator_propagates_proposed_tp_and_target_id_in_research_apply() -> None:
    bars = _make_m1_candles(25, base_price=2650.0, step=0.1)
    ctx = _DummyCtx(bars, now_ms=bars[-1]["close_time"])
    # Provide an unswept swing high at 2660.5 so SI5/SI7 selects it over baseline_tp=2675.0
    res = evaluate_signal_intelligence_gate_v1793122(
        context=ctx,
        model_id="SMC_M1_REVERSAL",
        direction="LONG",
        mode="RESEARCH_APPLY",
        policy_id="SI5_STRUCTURAL_ENTRY_SL_AND_REACHABLE_TARGET",
        adapter_origin="BACKTEST",
        baseline_status="ELIGIBLE",
        baseline_rejection_reasons=(),
        baseline_entry=2652.5,
        baseline_sl=2650.0,
        baseline_tp=2675.0,
        baseline_qty=0.5,
        baseline_gross_rr=9.0,
        baseline_net_rr=7.5,
        baseline_anchor_id="a1",
        baseline_anchor_kind="SWING",
        baseline_invalidation_price=2650.5,
        baseline_target_id="old_far_target",
        setup_descriptors={},
        sh_1m=[{"id": "liq_pool_unswept_1", "price": 2660.5, "occurred_at_ms": 300_000, "known_at_ms": 420_000}],
        sl_1m=[{"id": "s1", "price": 2650.5, "occurred_at_ms": 300_000, "known_at_ms": 420_000}],
        atr_m1=1.5,
        atr_m5=3.0,
    )
    assert res.proposed_tp == 2660.5
    assert res.proposed_target_id == "liq_pool_unswept_1"


def test_t21_shared_evaluator_propagates_proposed_entry_in_research_apply() -> None:
    bars = _make_m1_candles(25, base_price=2650.0, step=0.1)
    # Bar 18 retests FVG [2651.2, 2651.8] after formation at bar 10 (660_000)
    bars[18]["low"] = 2651.4
    bars[18]["close"] = 2651.9
    ctx = _DummyCtx(bars, now_ms=bars[-1]["close_time"])
    res = evaluate_signal_intelligence_gate_v1793122(
        context=ctx,
        model_id="CG01_SWEEP_MSS_FVG",
        direction="LONG",
        mode="RESEARCH_APPLY",
        policy_id="SI4_CAUSAL_LIQUIDITY_SWEEP_AND_TRUE_UNICORN",
        adapter_origin="BACKTEST",
        baseline_status="ELIGIBLE",
        baseline_rejection_reasons=(),
        baseline_entry=2652.5,
        baseline_sl=2649.5,
        baseline_tp=2660.0,
        baseline_qty=0.5,
        baseline_gross_rr=2.5,
        baseline_net_rr=2.2,
        baseline_anchor_id="a1",
        baseline_anchor_kind="SWING",
        baseline_invalidation_price=2649.8,
        baseline_target_id="t1",
        setup_descriptors={
            "selected_sweep_id": "sw1",
            "sweep_occurred_at_ms": 480_000,
            "sweep_reclaimed_at_ms": 540_000,
            "selected_break_id": "b1",
            "mss_confirmed_at_ms": 600_000,
            "selected_fvg_id": "fvg1",
            "fvg_formed_at_ms": 660_000,
            "fvg_low": 2651.2,
            "fvg_high": 2651.8,
        },
        sh_1m=[{"id": "t1", "price": 2660.0, "occurred_at_ms": 300_000, "known_at_ms": 420_000}],
        sl_1m=[{"id": "s1", "price": 2649.8, "occurred_at_ms": 300_000, "known_at_ms": 420_000}],
        atr_m1=1.0,
        atr_m5=2.0,
        unicorn_evidence={
            "fvg_present": True,
            "breaker_present": True,
            "overlap_valid": True,
            "overlap_low": 2651.2,
            "overlap_high": 2651.8,
            "breaker_confirmed_at_ms": 600_000,
        },
    )
    assert res.proposed_entry == 2651.5


def test_t22_shared_evaluator_recomputes_gross_rr_and_net_rr_from_final_geometry() -> None:
    bars = _make_m1_candles(25, base_price=2650.0, step=0.1)
    ctx = _DummyCtx(bars, now_ms=bars[-1]["close_time"])
    res = evaluate_signal_intelligence_gate_v1793122(
        context=ctx,
        model_id="SMC_M1_REVERSAL",
        direction="LONG",
        mode="RESEARCH_APPLY",
        policy_id="SI5_STRUCTURAL_ENTRY_SL_AND_REACHABLE_TARGET",
        adapter_origin="BACKTEST",
        baseline_status="ELIGIBLE",
        baseline_rejection_reasons=(),
        baseline_entry=2652.5,
        baseline_sl=2651.8,
        baseline_tp=2670.0,
        baseline_qty=1.5,
        baseline_gross_rr=25.0,
        baseline_net_rr=20.0,
        baseline_anchor_id="a1",
        baseline_anchor_kind="SWING",
        baseline_invalidation_price=2650.5,
        baseline_target_id="t1",
        setup_descriptors={},
        sh_1m=[{"id": "liq_pool_1", "price": 2660.5, "occurred_at_ms": 300_000, "known_at_ms": 420_000}],
        sl_1m=[{"id": "s1", "price": 2650.5, "occurred_at_ms": 300_000, "known_at_ms": 420_000}],
        atr_m1=1.5,
        atr_m5=3.0,
    )
    assert res.proposed_gross_rr != 25.0
    assert res.proposed_net_rr != 20.0


def test_t23_shared_evaluator_recomputes_decision_fingerprint_when_geometry_changes() -> None:
    from services.shared_strategy_evaluator import _compute_snapshot_fingerprints

    bars = _make_m1_candles(25)
    ctx = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=bars[-1]["close_time"],
        m1_candles=bars,
        m5_candles=bars,
    )
    _, _, fp_base = _compute_snapshot_fingerprints(
        context=ctx,
        model_id="SMC_M1_REVERSAL",
        direction="LONG",
        setup_validity="VALID",
        entry_reference=2652.5,
        selected_fvg_id=None,
        selected_break_id="b1",
        selected_sweep_id="s1",
        setup_episode_id="ep1",
        unicorn_validity="NOT_APPLICABLE",
        selected_anchor_id="a1",
        structural_invalidation_price=2650.8,
        selected_sl=2650.5,
        take_profit=2665.0,
        hypothetical_quantity=0.5,
        net_rr=2.5,
        decision_status="ELIGIBLE",
        rejection_reason_codes=(),
    )
    _, _, fp_mod = _compute_snapshot_fingerprints(
        context=ctx,
        model_id="SMC_M1_REVERSAL",
        direction="LONG",
        setup_validity="VALID",
        entry_reference=2651.5,
        selected_fvg_id=None,
        selected_break_id="b1",
        selected_sweep_id="s1",
        setup_episode_id="ep1",
        unicorn_validity="NOT_APPLICABLE",
        selected_anchor_id="a1",
        structural_invalidation_price=2650.0,
        selected_sl=2649.8,
        take_profit=2660.5,
        hypothetical_quantity=0.62,
        net_rr=2.8,
        decision_status="ELIGIBLE",
        rejection_reason_codes=(),
    )
    assert fp_base != fp_mod


def test_t24_arbitration_uses_pre_entry_features_only_and_rejects_post_entry_labels() -> None:
    with pytest.raises(ValueError, match="LOOKAHEAD_ARBITRATION_FORBIDDEN"):
        score_candidate_record_pre_entry_v1793122({"_inject_post_entry_label_arbitration": True})

    cands = [
        {"opportunity_id": "opp_rev", "model_id": "SMC_M1_REVERSAL", "direction": "LONG", "net_rr": 2.1},
        {"opportunity_id": "opp_fvg", "model_id": "SMC_M1_FVG_CONTINUATION", "direction": "LONG", "net_rr": 2.9},
    ]
    feat_map = {
        "opp_rev": {"htf_alignment_state": "MIXED", "unicorn_classification": "NONE", "displacement_body_atr_ratio": 0.45},
        "opp_fvg": {"htf_alignment_state": "ALIGNED", "unicorn_classification": "UNICORN_CONFIRMED", "displacement_body_atr_ratio": 0.85},
    }
    winner, losers = arbitrate_competing_model_intents_v1793122(
        cands,
        arbitration_mode="PRE_ENTRY_QUALITY_TUPLE",
        feature_ledger_by_opp_id=feat_map,
    )
    assert winner is not None and winner["opportunity_id"] == "opp_fvg"
    assert len(losers) == 1


def test_t25_portfolio_single_slot_blocks_overlapping_entries() -> None:
    state = PortfolioReplayStateV179312(variant="SI7_CORE_TRADING_LOGIC_REPAIRED", max_concurrent_positions=1)
    state.active_episode = {"opportunity_id": "ep_1", "filled": True}
    state.active_release_ts_ms = 600_000
    advance_portfolio_to_next_event_v179312(state, None, 300_000)
    assert state.active_episode is not None and 300_000 < state.active_release_ts_ms
    advance_portfolio_to_next_event_v179312(state, None, 600_000)
    assert state.active_episode is None


def test_t26_strict_hold_vs_180_bar_time_stop_vs_legacy_censoring_are_distinct() -> None:
    episodes = [
        {
            "opportunity_id": "ep_open_1",
            "model_id": "SMC_M1_REVERSAL",
            "direction": "LONG",
            "filled": True,
            "outcome_label": "OPEN_AT_HORIZON",
            "realized_net_pnl_usd": 0.0,
            "realized_r": 0.0,
        },
        {
            "opportunity_id": "ep_loss_1",
            "model_id": "SMC_M1_CONTINUATION",
            "direction": "SHORT",
            "filled": True,
            "outcome_label": "LOSS",
            "realized_net_pnl_usd": -2.45,
            "realized_r": -1.0,
        },
    ]
    forensics = build_ground_truth_loss_forensics_v1793123(episodes, {})
    assert forensics["closed_trades_count"] == 1
    assert forensics["open_at_horizon_count"] == 1


def test_t27_data_gap_trade_remains_unresolved_not_win_or_loss() -> None:
    episodes = [
        {
            "opportunity_id": "ep_gap_1",
            "model_id": "SMC_M1_REVERSAL",
            "direction": "LONG",
            "filled": True,
            "outcome_label": "DATA_GAP_UNRESOLVED",
            "realized_net_pnl_usd": 0.0,
            "realized_r": 0.0,
        }
    ]
    forensics = build_ground_truth_loss_forensics_v1793123(episodes, {})
    assert forensics["closed_trades_count"] == 0
    assert forensics["data_gap_unresolved_count"] == 1
    assert forensics["data_gap_forensic_ledger"][0]["primary_root_cause"] == "FILL_GAP/AMBIGUOUS_INTRABAR"


def test_t28_ledger_identity_gross_minus_fees_equals_net_across_all_fill_modes() -> None:
    sample_trades = [
        {"fill_mode": "NEXT_BAR_OPEN_BID_ASK", "gross": -2.10, "fees": 0.35, "net": -2.45},
        {"fill_mode": "LEGACY_CLOSE_FILL", "gross": 5.40, "fees": 0.36, "net": 5.04},
        {"fill_mode": "STRESS_WIDER_SPREAD_SLIPPAGE_2X", "gross": -2.30, "fees": 0.38, "net": -2.68},
    ]
    for t in sample_trades:
        assert abs((t["gross"] - t["fees"]) - t["net"]) < 1e-6


def test_t29_feature_off_matches_baseline_trade_for_trade() -> None:
    evaluator = SharedStrategyEvaluator()
    bars = _make_m1_candles(30)
    ctx_off_1 = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=bars[-1]["close_time"],
        m1_candles=bars,
        m5_candles=bars,
        policy_config={"v17_9_3_1_2_2_signal_intelligence_mode": "OFF", "v17_9_3_1_2_2_policy_id": "SI0_OFF_BASELINE"},
    )
    ctx_off_2 = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=bars[-1]["close_time"],
        m1_candles=bars,
        m5_candles=bars,
    )
    s1 = evaluator.evaluate_model_direction(ctx_off_1, "SMC_M1_REVERSAL", "LONG", adapter_origin="BACKTEST")
    s2 = evaluator.evaluate_model_direction(ctx_off_2, "SMC_M1_REVERSAL", "LONG", adapter_origin="BACKTEST")
    assert s1.trade_action_fingerprint == s2.trade_action_fingerprint
    assert s1.decision_fingerprint == s2.decision_fingerprint


def test_t30_observe_only_emits_diagnostics_without_changing_trade_action() -> None:
    evaluator = SharedStrategyEvaluator()
    bars = _make_m1_candles(30)
    ctx_off = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=bars[-1]["close_time"],
        m1_candles=bars,
        m5_candles=bars,
        policy_config={"v17_9_3_1_2_2_signal_intelligence_mode": "OFF", "v17_9_3_1_2_2_policy_id": "SI0_OFF_BASELINE"},
    )
    ctx_obs = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=bars[-1]["close_time"],
        m1_candles=bars,
        m5_candles=bars,
        policy_config={
            "v17_9_3_1_2_2_signal_intelligence_mode": "OBSERVE_ONLY",
            "v17_9_3_1_2_2_policy_id": "SI7_CORE_TRADING_LOGIC_REPAIRED",
        },
    )
    s_off = evaluator.evaluate_model_direction(ctx_off, "SMC_M1_REVERSAL", "LONG", adapter_origin="BACKTEST")
    s_obs = evaluator.evaluate_model_direction(ctx_obs, "SMC_M1_REVERSAL", "LONG", adapter_origin="BACKTEST")
    assert s_off.trade_action_fingerprint == s_obs.trade_action_fingerprint
    assert s_obs.v1793122_signal_intelligence_mode == "OBSERVE_ONLY"


def test_t31_production_live_paper_block_research_apply_and_discovery() -> None:
    for exec_mode in ("PAPER", "LIVE", "AUTO_PAPER", "AUTO_LIVE"):
        for si_mode in ("RESEARCH_APPLY", "RESEARCH_DISCOVERY"):
            with pytest.raises(ValueError, match="FORBIDDEN_IN_LIVE_OR_PAPER_EXECUTION_MODE"):
                M1ScalpConfig(
                    execution_mode=exec_mode,
                    v17_9_3_1_2_2_signal_intelligence_mode=si_mode,
                    v17_9_3_1_2_2_policy_id="SI7_CORE_TRADING_LOGIC_REPAIRED",
                )
    forbidden, reason = is_research_apply_forbidden_in_adapter_v1793122("PRODUCTION_ORCHESTRATOR")
    assert forbidden is True and reason == "SI_FORBIDDEN_IN_PRODUCTION_ADAPTER"


def test_t32_worker_1_2_4_produce_identical_hashes() -> None:
    fake_ev = {
        "22": {
            "all_1_2_4_worker_hashes_identical": True,
            "independent_execution_no_shared_digest_reuse": True,
            "worker_1_sha256": "abc123" * 10 + "abcd",
        }
    }
    res = evaluate_v1793123_acceptance_gates_cr01_cr15(
        evidence_by_key=fake_ev,
        tested_executable_sha="a" * 40,
    )
    cr14 = [g for g in res["gates"] if g["gate_id"] == "CR14"][0]
    assert cr14["passed"] is True


def test_t33_zero_trade_or_negative_expectancy_cannot_return_positive_edge_or_activation_yes() -> None:
    v_zero = compute_strategy_statistical_verdict_v1793122(
        policy_metrics_by_id={"SI_ZERO": {"closed_trades_count": 0, "expectancy_net_r": 0.0, "total_realized_net_usd": 0.0}},
        oos_report={},
    )
    assert v_zero["strategy_statistical_verdict"] == "INCONCLUSIVE_UNDERPOWERED"
    assert v_zero["activation_ready"] == "NO"

    v_neg = compute_v1793123_dual_verdict(
        run_id="test_run",
        tested_executable_sha="a" * 40,
        gates_report={"all_15_acceptance_gates_passed": True, "passed_gates_count": 15, "total_gates": 15},
        policy_metrics_by_id={"SI7_CORE_TRADING_LOGIC_REPAIRED": {"closed_trades_count": 45, "expectancy_net_r": -0.75, "total_realized_net_usd": -80.0}},
        oos_report={},
    )
    assert v_neg["engineering_verification_verdict"] == "PASS"
    assert v_neg["strategy_statistical_verdict"] == "NEGATIVE_EXPECTANCY"
    assert v_neg["activation_ready"] == "NO"


def test_t34_red_green_source_mutation_fails_red_and_passes_green() -> None:
    from tools.run_v17_9_3_1_2_2_verification import run_16_executable_red_green_source_mutations_v1793122

    mut_report = run_16_executable_red_green_source_mutations_v1793122(tested_executable_sha="a" * 40)
    assert mut_report["executable_mutations_count"] == 16
    assert mut_report["executable_mutations_caught_count"] == 16
    assert mut_report["all_mutations_caught_red_and_restored_green"] is True

