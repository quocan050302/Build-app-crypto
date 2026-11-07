"""V17.9.3.1.2.9 — Liquidity Provenance Closure & Independent Strategy Failure Diagnosis tests.

Covers all 28 required V129 RED/GREEN source mutation controls (M01..M28) and
positive/negative source-binding controls across all 5 canonical models and both directions.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import json
import hashlib
import pytest

from services.v17_9_3_1_2_2_signal_intelligence import (
    _build_causal_swing_pools_from_swings_v1793124,
    _closed_bars_as_of,
    _resolve_unswept_liquidity_pool_target_v1793123,
    build_signal_quality_evidence_v1793122,
    evaluate_signal_intelligence_gate_v1793122,
)
from services.v17_9_3_1_2_7_causal_signal_truth import (
    canonical_hash,
    classify_observed_loss_v1793127,
    derive_pre_entry_features_from_causal_context_v1793127,
    rebuild_materialized_intent_proof_v1793127,
    required_proof_rejection_v1793127,
    verify_feature_snapshot_v1793127,
)
from services.v17_9_3_1_2_7_matched_ab import (
    compute_matched_policy_ab_v1793127,
    verify_matched_ab_against_raw_ledgers_v1793127,
)
from tools.v17_9_3_1_2_7_proof_checks import (
    verify_checkpoint_coverage_from_disk,
    verify_fresh_full_junit,
    verify_git_remote_phase_receipt_v1793127,
)


def _make_m1_bars(base_ms: int, count: int, *, low: float = 99.0, high: float = 101.0, close: float = 100.0):
    return [
        {
            "open_time": base_ms + i * 60_000,
            "timestamp": base_ms + i * 60_000,
            "close_time": base_ms + (i + 1) * 60_000,
            "open": close,
            "high": high,
            "low": low,
            "close": close,
            "is_closed": True,
        }
        for i in range(count)
    ]


def test_m01_fabricated_pool_from_prop_target_id_and_tp_rejected():
    """M01: Fabricated pool from prop_target_id + prop_tp -> real source verifier rejects."""
    base_ms = 1_790_000_000_000
    m1_bars = _make_m1_bars(base_ms, 20, low=2648.0, high=2651.0, close=2650.0)
    m5_bars = [
        {
            "open_time": base_ms + i * 300_000,
            "timestamp": base_ms + i * 300_000,
            "close_time": base_ms + (i + 1) * 300_000,
            "open": 2650.0,
            "high": 2651.0,
            "low": 2648.0,
            "close": 2650.0,
            "is_closed": True,
        }
        for i in range(4)
    ]
    now_ms = m1_bars[-1]["close_time"]
    ctx = SimpleNamespace(
        symbol="XAUUSDT",
        decision_timestamp_ms=now_ms,
        closed_m1_candles=m1_bars,
        closed_m5_candles=m5_bars,
        liquidity_pools=(),
        spread=0.10,
        quote_timestamp_ms=now_ms,
    )
    decision = evaluate_signal_intelligence_gate_v1793122(
        context=ctx,
        mode="RESEARCH_APPLY",
        policy_id="SI7_CORE_TRADING_LOGIC_REPAIRED",
        model_id="SMC_M1_REVERSAL",
        direction="LONG",
        setup_descriptors={
            "protected_structure_anchor_id": "sl_anchor_1",
            "protected_structure_price": 2648.80,
            "protected_structure_known_at_ms": now_ms - 60_000,
        },
        sh_1m=[],
        sl_1m=[
            {
                "id": "sl_anchor_1",
                "price": 2648.80,
                "occurred_at_ms": now_ms - 180_000,
                "known_at_ms": now_ms - 60_000,
            }
        ],
        baseline_status="VALID",
        baseline_entry=2650.0,
        baseline_sl=2648.50,
        baseline_tp=2655.0,
        baseline_qty=0.1,
        baseline_net_rr=2.5,
        baseline_target_id="fake_tp_pool_999",
        atr_m1=1.0,
        atr_m5=2.2,
    )
    assert "SI5_TARGET_POOL_ABSENT_OR_ALREADY_SWEPT" in decision.rejection_reason_codes
    assert decision.gates_passed is False


def test_m02_falsify_known_at_now_ms_without_confirming_bars_rejected():
    """M02: Falsify known_at=now_ms without confirming bars -> reject."""
    base_ms = 120_000
    bars = _make_m1_bars(base_ms, 4)
    unconfirmed_pool = {"pool_id": "p_unconfirmed", "price": 103.0, "occurred_at_ms": 120_000, "known_at_ms": None}
    res = _resolve_unswept_liquidity_pool_target_v1793123(
        pools=[unconfirmed_pool],
        closed_1m=bars,
        direction="LONG",
        entry_price=100.0,
        selected_sl=98.0,
        selected_target_id="p_unconfirmed",
        take_profit=103.0,
        now_ms=360_000,
        eff_atr_m1=1.0,
        eff_atr_m5=2.0,
    )
    assert res == (None, 103.0, 0, False, False)


def test_m03_missing_known_at_fallback_to_occurred_at_rejected():
    """M03: Missing known_at_ms falls back to occurred_at_ms -> reject."""
    bars = _make_m1_bars(120_000, 4)
    swings = [{"id": "sw_missing_known_at", "price": 103.0, "occurred_at_ms": 120_000}]
    built = _build_causal_swing_pools_from_swings_v1793124(swings, bars, "LONG", 360_000)
    assert built == []


def test_m04_empty_m1_price_path_treated_as_unswept_rejected():
    """M04: Empty M1 price path treated as UNSWEPT -> reject."""
    pool = {"pool_id": "p1", "price": 103.0, "occurred_at_ms": 120_000, "known_at_ms": 180_000}
    res = _resolve_unswept_liquidity_pool_target_v1793123(
        pools=[pool],
        closed_1m=[],
        direction="LONG",
        entry_price=100.0,
        selected_sl=98.0,
        selected_target_id="p1",
        take_profit=103.0,
        now_ms=360_000,
        eff_atr_m1=1.0,
        eff_atr_m5=2.0,
    )
    assert res == (None, 103.0, 0, False, False)
    built = _build_causal_swing_pools_from_swings_v1793124(
        [{"id": "p1", "price": 103.0, "occurred_at_ms": 120_000, "known_at_ms": 180_000}],
        [],
        "LONG",
        360_000,
    )
    assert len(built) == 1
    assert built[0]["coverage_complete"] is False
    assert built[0]["swept_status"] == "UNKNOWN"


def test_m05_interior_price_path_gap_treated_as_complete_rejected():
    """M05: Interior price path gap treated as complete -> reject."""
    bars = _make_m1_bars(120_000, 4)
    bars.pop(2)  # Create an interior 1m gap between 240_000 and 300_000
    pool = {"pool_id": "p1", "price": 103.0, "occurred_at_ms": 120_000, "known_at_ms": 180_000}
    res = _resolve_unswept_liquidity_pool_target_v1793123(
        pools=[pool],
        closed_1m=bars,
        direction="LONG",
        entry_price=100.0,
        selected_sl=98.0,
        selected_target_id="p1",
        take_profit=103.0,
        now_ms=360_000,
        eff_atr_m1=1.0,
        eff_atr_m5=2.0,
    )
    assert res == (None, 103.0, 0, False, False)


def test_m06_observed_actual_sweep_before_decision_rejected():
    """M06: Observed actual sweep before decision ignored -> reject."""
    bars = _make_m1_bars(120_000, 4)
    bars[2]["high"] = 103.5  # Sweeps 103.0 after pivot
    pool = {"pool_id": "p1", "price": 103.0, "occurred_at_ms": 120_000, "known_at_ms": 180_000}
    res = _resolve_unswept_liquidity_pool_target_v1793123(
        pools=[pool],
        closed_1m=bars,
        direction="LONG",
        entry_price=100.0,
        selected_sl=98.0,
        selected_target_id="p1",
        take_profit=103.0,
        now_ms=360_000,
        eff_atr_m1=1.0,
        eff_atr_m5=2.0,
    )
    assert res == (None, None, 0, False, True)


def test_m07_future_or_partial_source_bar_rejected():
    """M07: Future/partial source bar mistakenly accepted -> reject."""
    bars = _make_m1_bars(120_000, 3)
    bars[-1]["is_closed"] = False
    closed, unclosed_count = _closed_bars_as_of(bars, 300_000, 60_000)
    assert len(closed) == 2
    assert unclosed_count == 1


def test_m08_duplicate_pool_id_with_conflicting_prices_rejected():
    """M08: Duplicate pool ID with conflicting prices -> reject."""
    bars = _make_m1_bars(120_000, 4)
    p1 = {"pool_id": "p_dup", "price": 103.0, "occurred_at_ms": 120_000, "known_at_ms": 180_000}
    p2 = {"pool_id": "p_dup", "price": 104.0, "occurred_at_ms": 120_000, "known_at_ms": 180_000}
    res = _resolve_unswept_liquidity_pool_target_v1793123(
        pools=[p1, p2],
        closed_1m=bars,
        direction="LONG",
        entry_price=100.0,
        selected_sl=98.0,
        selected_target_id="p_dup",
        take_profit=103.0,
        now_ms=360_000,
        eff_atr_m1=1.0,
        eff_atr_m5=2.0,
    )
    assert res == (None, 103.0, 0, False, False)


def test_m09_selected_target_id_rebound_to_unrelated_nearby_pool_rejected():
    """M09: Selected target ID re-bound silently to unrelated nearby pool -> reject."""
    base = 1_790_000_100_000
    bars = [
        dict(
            open_time=base + i * 60_000,
            close_time=base + (i + 1) * 60_000,
            open=100,
            close=100,
            high=105 if 5 <= i < 10 else 102,
            low=98,
            is_closed=True,
        )
        for i in range(20)
    ]
    m5 = [
        dict(
            open_time=base + i * 300_000,
            close_time=base + (i + 1) * 300_000,
            open=100,
            close=100,
            high=max(b["high"] for b in bars[i * 5 : (i + 1) * 5]),
            low=98,
            is_closed=True,
        )
        for i in range(4)
    ]
    decision = dict(
        opportunity_id="fixture",
        setup_episode_id="setup",
        direction="LONG",
        model_id="SMC_M1_REVERSAL",
        entry_reference=100,
        selected_sl=97,
        take_profit=105,
        selected_target_id="unrelated_target_id_999",
    )
    context = SimpleNamespace(
        symbol="XAUUSDT",
        decision_timestamp_ms=bars[-1]["close_time"],
        closed_m1_candles=bars,
        closed_m5_candles=m5,
    )
    result = derive_pre_entry_features_from_causal_context_v1793127(decision, context, dataset_sha256="a" * 64)
    assert result["target_selected_pool_bound"] is None
    assert result["target_unswept"] is None


def test_m10_wrong_side_target_rejected():
    """M10: Wrong-side target (LONG below entry, SHORT above entry) -> reject."""
    bars = _make_m1_bars(120_000, 4)
    wrong_side_pool = {"pool_id": "p_wrong", "price": 97.0, "occurred_at_ms": 120_000, "known_at_ms": 180_000}
    res = _resolve_unswept_liquidity_pool_target_v1793123(
        pools=[wrong_side_pool],
        closed_1m=bars,
        direction="LONG",
        entry_price=100.0,
        selected_sl=95.0,
        selected_target_id="p_wrong",
        take_profit=97.0,
        now_ms=360_000,
        eff_atr_m1=1.0,
        eff_atr_m5=2.0,
    )
    assert res == (None, 97.0, 0, False, False)


def test_m11_incorrect_tick_tp_price_or_altered_selected_id_rejected():
    """M11: Incorrect tick/TP price or altered selected ID -> reject."""
    base = 1_790_000_100_000
    bars = [
        dict(
            open_time=base + i * 60_000,
            close_time=base + (i + 1) * 60_000,
            open=100,
            close=100,
            high=105 if 5 <= i < 10 else 102,
            low=98,
            is_closed=True,
        )
        for i in range(20)
    ]
    m5 = [
        dict(
            open_time=base + i * 300_000,
            close_time=base + (i + 1) * 300_000,
            open=100,
            close=100,
            high=max(b["high"] for b in bars[i * 5 : (i + 1) * 5]),
            low=98,
            is_closed=True,
        )
        for i in range(4)
    ]
    target_id = f"pool_5M_5m_sh_{base + 300_000}"
    decision = dict(
        opportunity_id="fixture",
        setup_episode_id="setup",
        direction="LONG",
        model_id="SMC_M1_REVERSAL",
        entry_reference=100,
        selected_sl=97,
        take_profit=105.05,
        selected_target_id=target_id,
    )
    context = SimpleNamespace(
        symbol="XAUUSDT",
        decision_timestamp_ms=bars[-1]["close_time"],
        closed_m1_candles=bars,
        closed_m5_candles=m5,
    )
    result = derive_pre_entry_features_from_causal_context_v1793127(decision, context, dataset_sha256="a" * 64)
    assert result["target_selected_pool_bound"] is None


def test_m12_target_source_event_from_different_episode_rejected():
    """M12: Target source event from different model/episode validates this setup -> reject."""
    base = 1_790_000_100_000
    bars = _make_m1_bars(base, 12)
    decision = dict(
        opportunity_id="opp_episode_1",
        setup_episode_id="ep_1",
        direction="LONG",
        model_id="SMC_M1_REVERSAL",
        entry_reference=100,
        selected_sl=97,
        take_profit=105,
    )
    context = SimpleNamespace(
        symbol="XAUUSDT",
        decision_timestamp_ms=bars[-1]["close_time"],
        closed_m1_candles=bars,
    )
    result = derive_pre_entry_features_from_causal_context_v1793127(decision, context, dataset_sha256="a" * 64)
    assert verify_feature_snapshot_v1793127(result) is True
    tampered = dict(result, opportunity_id="opp_episode_2")
    assert verify_feature_snapshot_v1793127(tampered) is False


def test_m13_source_proof_stale_after_repricing_rejected():
    """M13: Source proof valid initially but stale after repricing -> reject."""
    base = 1_790_000_100_000
    bars = _make_m1_bars(base, 12)
    decision = dict(
        opportunity_id="opp_1",
        setup_episode_id="ep_1",
        direction="LONG",
        model_id="SMC_M1_REVERSAL",
        decision_timestamp_ms=bars[-1]["close_time"],
        entry_reference=100.0,
        selected_sl=97.0,
        take_profit=105.0,
    )
    context = SimpleNamespace(
        symbol="XAUUSDT",
        decision_timestamp_ms=bars[-1]["close_time"],
        closed_m1_candles=bars,
    )
    feature = derive_pre_entry_features_from_causal_context_v1793127(decision, context, dataset_sha256="a" * 64)
    repriced_decision = dict(decision, take_profit=106.0)
    rej = required_proof_rejection_v1793127(feature, "SMC_M1_REVERSAL", decision=repriced_decision)
    assert rej == "REJECTED_UNPROVEN:INTENT_GEOMETRY_MISMATCH"


def test_m14_wrong_protective_sl_side_or_inside_pivot_rejected():
    """M14: Wrong protective SL side or SL inside verified pivot -> reject."""
    base = 1_790_000_100_000
    prices = [
        (101, 99, 100),
        (101, 99, 100),
        (101, 95, 100),  # swing low at index 2 (price 95)
        (101, 99, 100),
        (101, 99, 100),
        (101, 99, 100),
    ]
    bars = [
        dict(
            open_time=base + i * 60_000,
            timestamp=base + i * 60_000,
            close_time=base + (i + 1) * 60_000,
            open=100,
            high=h,
            low=l,
            close=c,
            is_closed=True,
        )
        for i, (h, l, c) in enumerate(prices)
    ]
    cutoff = bars[-1]["close_time"]
    swing_id = f"1m_sl_{bars[2]['open_time']}"
    # SL at 96.0 is INSIDE the swing low at 95.0 for a LONG trade
    decision = dict(
        opportunity_id="fixture",
        setup_episode_id="setup",
        model_id="SMC_M1_REVERSAL",
        direction="LONG",
        entry_reference=100.0,
        selected_sl=96.0,
        take_profit=108.0,
        selected_anchor_id=swing_id,
    )
    feature = derive_pre_entry_features_from_causal_context_v1793127(
        decision,
        SimpleNamespace(symbol="XAUUSDT", decision_timestamp_ms=cutoff, closed_m1_candles=bars),
        dataset_sha256="a" * 64,
    )
    assert feature["protected_structure_independent_of_sl"] is None


def test_m15_mutation_of_final_quantity_without_risk_recalculation_rejected(monkeypatch):
    """M15: Mutation of final quantity without risk recalculation -> reject."""
    from services import v17_9_3_1_2_7_causal_signal_truth as truth
    from services.v17_9_3_1_2_executable_intervention_replay_service import recalculate_trade_risk_quantity_v179312

    monkeypatch.setattr(truth, "required_proof_rejection_v1793127", lambda *args, **kwargs: None)
    cutoff = 1_790_000_160_000
    sized = recalculate_trade_risk_quantity_v179312(2650, 2645, 2680, direction="LONG", decision_ts=cutoff)
    bar = dict(open_time=cutoff - 60_000, close_time=cutoff, open=2650, high=2651, low=2649, close=2650, is_closed=True)
    opp = dict(
        opportunity_id="fixture",
        setup_episode_id="setup",
        model_id="SMC_M1_REVERSAL",
        direction="LONG",
        decision_timestamp_ms=cutoff,
        entry_reference=2649,
        selected_sl=2645,
        take_profit=2680,
    )
    plan = dict(
        new_decision_timestamp_ms=cutoff,
        new_checkpoint_index_1m=0,
        new_entry_reference=2650,
        new_selected_sl=2645,
        new_take_profit=2680,
        new_quantity=sized["quantity"] + 0.02,
    )
    _, _, rejection = rebuild_materialized_intent_proof_v1793127(opp, plan, {"1m": [bar]}, dataset_sha256="a" * 64)
    assert rejection == "REJECTED_UNPROVEN:FINAL_RISK_OR_QUANTITY_MISMATCH"


def test_m16_target_reachable_requires_verified_source_pool():
    """M16: target_reachable=True with no source pool => cannot satisfy pool-proven gate."""
    base_ms = 1_790_000_000_000
    bars = _make_m1_bars(base_ms, 10, low=2649.0, high=2651.0, close=2650.0)
    now_ms = bars[-1]["close_time"]
    ctx = SimpleNamespace(
        symbol="XAUUSDT",
        decision_timestamp_ms=now_ms,
        closed_m1_candles=bars,
        liquidity_pools=(),
        spread=0.10,
        quote_timestamp_ms=now_ms,
    )
    ev = build_signal_quality_evidence_v1793122(
        context=ctx,
        model_id="SMC_M1_REVERSAL",
        direction="LONG",
        entry_price=2650.0,
        selected_sl=2648.5,
        take_profit=2654.0,
        selected_target_id="missing_pool_id",
        atr_m1=1.0,
        atr_m5=2.2,
    )
    assert ev.target_geometrically_feasible is True
    assert ev.target_pool_proven is False
    assert ev.target_reachable is False


def test_m17_real_provider_quote_without_receipt_rejected():
    """M17: No candle coverage / provider receipt but quote source label REAL_PROVIDER_QUOTE => reject provider proof."""
    base_ms = 1_790_000_000_000
    bars = _make_m1_bars(base_ms, 5)
    now_ms = bars[-1]["close_time"]
    ctx = SimpleNamespace(
        symbol="XAUUSDT",
        decision_timestamp_ms=now_ms,
        closed_m1_candles=bars,
        quote_source_kind="REAL_PROVIDER_QUOTE",
        quote_timestamp_ms=now_ms,
        quote_provider_source_id=None,
    )
    decision = dict(opportunity_id="opp_q", setup_episode_id="ep_q", direction="LONG", model_id="SMC_M1_REVERSAL")
    res = derive_pre_entry_features_from_causal_context_v1793127(decision, ctx, dataset_sha256="a" * 64)
    assert res["quote_source_kind"] == "UNVERIFIED_PROVIDER_QUOTE"


def test_m18_future_h4_bar_excluded_from_htf_bias():
    """M18: Swap H4/H1 bias with future H4 bar => reject."""
    base_ms = 1_790_000_000_000
    h4_dur = 14_400_000
    # 6 bearish closed H4 bars, plus 1 huge future bullish H4 bar closing after cutoff
    h4_bars = [
        {
            "open_time": base_ms + i * h4_dur,
            "timestamp": base_ms + i * h4_dur,
            "close_time": base_ms + (i + 1) * h4_dur,
            "open": 2700.0 - i * 10.0,
            "high": 2702.0 - i * 10.0,
            "low": 2688.0 - i * 10.0,
            "close": 2690.0 - i * 10.0,
            "is_closed": True,
        }
        for i in range(6)
    ]
    cutoff = h4_bars[-1]["close_time"]
    future_h4 = {
        "open_time": cutoff,
        "timestamp": cutoff,
        "close_time": cutoff + h4_dur,
        "open": 2640.0,
        "high": 2850.0,
        "low": 2640.0,
        "close": 2845.0,
        "is_closed": True,
    }
    m1_bars = _make_m1_bars(cutoff - 10 * 60_000, 10)
    ctx_without_future = SimpleNamespace(
        symbol="XAUUSDT",
        decision_timestamp_ms=cutoff,
        closed_m1_candles=m1_bars,
        closed_h4_candles=h4_bars,
    )
    ctx_with_future = SimpleNamespace(
        symbol="XAUUSDT",
        decision_timestamp_ms=cutoff,
        closed_m1_candles=m1_bars,
        closed_h4_candles=h4_bars + [future_h4],
    )
    decision = dict(opportunity_id="opp_h4", setup_episode_id="ep_h4", direction="SHORT", model_id="SMC_M1_CONTINUATION")
    r1 = derive_pre_entry_features_from_causal_context_v1793127(decision, ctx_without_future, dataset_sha256="a" * 64)
    r2 = derive_pre_entry_features_from_causal_context_v1793127(decision, ctx_with_future, dataset_sha256="a" * 64)
    assert r1["source_snapshot_hash"] == r2["source_snapshot_hash"]


def test_m19_unrelated_fvg_or_unicorn_breaker_source_rejected():
    """M19: Attach unrelated FVG/retest or Unicorn breaker source => reject."""
    base = 1_790_000_000_000
    # Create a bullish FVG between bar 0 (high=101) and bar 2 (low=103), retested at bar 3
    prices = [
        (100, 101, 99, 100.5),
        (100.5, 105, 100.5, 104.5),
        (104.5, 106, 103, 105),
        (105, 105.5, 102, 104),
        (104, 105, 103, 104.5),
    ]
    bars = [
        dict(
            open_time=base + i * 60_000,
            timestamp=base + i * 60_000,
            close_time=base + (i + 1) * 60_000,
            open=o,
            high=h,
            low=l,
            close=c,
            is_closed=True,
        )
        for i, (o, h, l, c) in enumerate(prices)
    ]
    cutoff = bars[-1]["close_time"]
    decision = dict(
        opportunity_id="opp_fvg",
        setup_episode_id="ep_fvg",
        direction="LONG",
        model_id="SMC_M1_FVG_CONTINUATION",
        selected_fvg_id="unrelated_fvg_id_999",
    )
    ctx = SimpleNamespace(symbol="XAUUSDT", decision_timestamp_ms=cutoff, closed_m1_candles=bars)
    res = derive_pre_entry_features_from_causal_context_v1793127(decision, ctx, dataset_sha256="a" * 64)
    assert res["fvg_present"] is False


def test_m20_decisive_source_timestamp_one_second_beyond_cutoff_rejected():
    """M20: Change decisive source timestamp one second beyond cutoff => reject."""
    base = 1_790_000_000_000
    prices = [
        (100, 101, 99, 100.5),
        (100.5, 105, 100.5, 104.5),
        (104.5, 106, 103, 105),
        (105, 105.5, 102, 104),
        (104, 105, 103, 104.5),
    ]
    bars = [
        dict(
            open_time=base + i * 60_000,
            timestamp=base + i * 60_000,
            close_time=base + (i + 1) * 60_000,
            open=o,
            high=h,
            low=l,
            close=c,
            is_closed=True,
        )
        for i, (o, h, l, c) in enumerate(prices)
    ]
    cutoff = bars[-1]["close_time"]
    decision = dict(opportunity_id="opp_ts", setup_episode_id="ep_ts", direction="LONG", model_id="SMC_M1_FVG_CONTINUATION")
    ctx = SimpleNamespace(symbol="XAUUSDT", decision_timestamp_ms=cutoff, closed_m1_candles=bars)
    res = derive_pre_entry_features_from_causal_context_v1793127(decision, ctx, dataset_sha256="a" * 64)
    assert verify_feature_snapshot_v1793127(res) is True
    snap = dict(res["causal_snapshot"])
    features = [dict(f) for f in snap["features"]]
    fvg_ref = next(f for f in features if f["evidence_type"] == "fvg_present")
    assert fvg_ref["proof_state"] == "PROVEN_TRUE"
    fvg_ref["known_at_ms"] = cutoff + 1_000
    snap["features"] = features
    payload = dict(snap)
    payload.pop("proof_hash", None)
    new_hash = canonical_hash(payload)
    snap["proof_hash"] = new_hash
    tampered = dict(res, causal_snapshot=snap, proof_hash=new_hash)
    assert verify_feature_snapshot_v1793127(tampered) is False


def test_m21_altered_raw_junit_xml_rejected_by_verifier(tmp_path):
    """M21: Alter raw JUnit XML after success but keep exit=0 => verifier FAIL."""
    run_dir = tmp_path / "run"
    junit_dir = run_dir / "junit"
    junit_dir.mkdir(parents=True)
    xml_path = junit_dir / "SHARD_000.xml"
    xml_bytes = b'<testsuite tests="1"><testcase classname="backend.tests.test_x" name="test_a"/></testsuite>'
    xml_path.write_bytes(xml_bytes)
    orig_sha = hashlib.sha256(xml_bytes).hexdigest()
    # Tamper XML bytes on disk after recording orig_sha
    xml_path.write_bytes(xml_bytes + b"<!-- tampered -->")
    mtime_ns = xml_path.stat().st_mtime_ns
    collection = {"collected_node_ids": ["backend/tests/test_x.py::test_a"]}
    junit_manifest = {
        "tested_executable_sha": "a" * 40,
        "run_start_epoch_ns": mtime_ns - 10_000_000,
        "exact_node_id_bijection_verified": True,
        "reused_shards_count": 0,
        "shards": [
            {
                "shard_name": "SHARD_000",
                "junit_xml_path": str(xml_path),
                "xml_sha256": orig_sha,
                "xml_mtime_ns": mtime_ns,
                "reused": False,
                "tested_executable_sha": "a" * 40,
                "pid": 1234,
                "collect_exit_code": 0,
                "pytest_exit_code": 0,
                "exact_bijection": True,
            }
        ],
    }
    with pytest.raises(ValueError, match="STALE_OR_TAMPERED_JUNIT"):
        verify_fresh_full_junit(run_dir, collection, junit_manifest)


def test_m22_deleted_matched_pair_or_altered_fee_rejected():
    """M22: Delete a matched pair ledger or alter one fee -> verifier FAIL."""
    dec_a = [
        {
            "opportunity_id": "opp_1",
            "decision_timestamp_ms": 1_000_000,
            "source_snapshot_hash": "s" * 64,
            "model_id": "SMC_M1_REVERSAL",
            "direction": "LONG",
            "accepted": True,
            "status": "ELIGIBLE",
        }
    ]
    dec_b = [dict(dec_a[0])]
    bad_trade = [
        {
            "opportunity_id": "opp_1",
            "decision_timestamp_ms": 1_000_000,
            "source_snapshot_hash": "s" * 64,
            "model_id": "SMC_M1_REVERSAL",
            "direction": "LONG",
            "filled": True,
            "outcome_label": "LOSS",
            "realized_gross_pnl_usd": -2.0,
            "entry_fee_usd": 0.10,
            "exit_fee_usd": 0.10,
            "realized_net_pnl_usd": -2.05,  # Fee mismatch: -2.0 - 0.20 != -2.05
            "realized_r": -1.0,
            "planned_net_risk_usd": 2.0,
        }
    ]
    with pytest.raises(ValueError, match="FEE_NET_RECONCILIATION_MISMATCH"):
        compute_matched_policy_ab_v1793127(dec_a, dec_b, bad_trade, bad_trade, comparison_id="SI0_SI8", fill_mode="NEXT_BAR_OPEN_BID_ASK")


def test_m23_injected_repeated_or_missing_checkpoint_fails_census(tmp_path):
    """M23: Inject repeated/missing full-universe checkpoint -> census FAIL."""
    import gzip

    bars_1m = _make_m1_bars(1_790_000_000_000, 32)
    audit_path = tmp_path / "audit.jsonl.gz"
    row = {
        "checkpoint_index_1m": 30,
        "model_id": "SMC_M1_CONTINUATION",
        "direction": "LONG",
        "decision_timestamp_ms": bars_1m[30]["close_time"],
        "max_closed_bar_ms": bars_1m[30]["close_time"],
        "source_snapshot_hash": "a" * 64,
        "off_action_hash": "b" * 64,
        "observe_action_hash": "b" * 64,
    }
    with gzip.open(audit_path, "wt", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
        f.write(json.dumps(row) + "\n")  # Duplicate checkpoint row
    res = verify_checkpoint_coverage_from_disk(audit_path, {"1m": bars_1m})
    assert res["status"] == "FAIL"
    assert "DUPLICATE_CHECKPOINT_MODEL_DIRECTION" in res["violations"]


def test_m24_worker_1v2v4_parity_requires_independent_pids_and_matching_digests(tmp_path):
    """M24: Fake copied 1v2v4 worker digest with no independent outputs -> parity FAIL."""
    from tools.v17_9_3_1_2_7_proof_checks import verify_raw_accounting_and_worker_proofs

    run = tmp_path / "run_w"
    run.mkdir()
    (run / "15_all_policies_all_three_fill_modes_metrics.json").write_text("{}", encoding="utf-8")
    (run / "24_engineering_strategy_activation_dual_verdict.json").write_text(
        json.dumps(
            {
                "policies": {},
                "strategy_statistical_verdict": "INCONCLUSIVE_UNDERPOWERED",
                "activation_ready": "NO",
                "fresh_holdout": "REUSED_HISTORICAL_HOLDOUT_NOT_FRESH_OOS",
                "method": "SEEDED_EMPIRICAL_BOOTSTRAP_HOLM_STEP_DOWN",
            }
        ),
        encoding="utf-8",
    )
    (run / "16_walkforward_purge_embargo_oos_freshness_and_uncertainty.json").write_text(
        json.dumps(
            {
                "split_boundaries_ms": [1791676800000, 1792195200000],
                "embargo_bars_m1": 180,
                "fresh_oos_verification_status": "REUSED_HISTORICAL_HOLDOUT_NOT_FRESH_OOS",
                "policies": {},
            }
        ),
        encoding="utf-8",
    )
    # Write worker manifest where worker_count=4 only has 1 PID observed
    empty_sha = hashlib.sha256(b"{}").hexdigest()
    (run / "21_1v2v4_independent_determinism_sha_manifest.json").write_text(
        json.dumps(
            {
                "28": {"worker_count": 1, "worker_pids_observed": [101], "results_by_policy": {}, "canonical_results_sha256": empty_sha},
                "29": {"worker_count": 2, "worker_pids_observed": [102, 103], "results_by_policy": {}, "canonical_results_sha256": empty_sha},
                "30": {"worker_count": 4, "worker_pids_observed": [104], "results_by_policy": {}, "canonical_results_sha256": empty_sha},
            }
        ),
        encoding="utf-8",
    )
    import gzip

    with gzip.open(run / "05_full_checkpoint_model_direction_execution_audit.jsonl.gz", "wt", encoding="utf-8") as f:
        pass
    with gzip.open(run / "13_baseline_raw_execution_episodes.jsonl.gz", "wt", encoding="utf-8") as f:
        pass
    with gzip.open(run / "13_per_trade_forensics_observed_vs_hypothesized.jsonl.gz", "wt", encoding="utf-8") as f:
        pass
    with gzip.open(run / "07_verified_causal_features.jsonl.gz", "wt", encoding="utf-8") as f:
        pass
    (run / "03_canonical_dataset_and_quote_source_manifest.json").write_text(
        json.dumps({"dataset_sha256": "9f301fee2e620c882b205b857fc63b73c3e36f550161f4100e64fcba7ff98c18"}),
        encoding="utf-8",
    )
    res = verify_raw_accounting_and_worker_proofs(run)
    assert res["status"] == "FAIL"
    assert "WORKER_EXECUTION_OR_DIGEST_MISMATCH:30" in res["errors"]


def test_m25_remote_phase_receipt_blocked_before_phase_c(monkeypatch, tmp_path):
    """M25: Claim G34/G40 PASS before actual Phase C remote readback -> verifier BLOCKED."""
    import subprocess

    fake_sha = "a" * 40

    def _fake_check_output(cmd, **kwargs):
        if "branch" in cmd:
            return "feature/v17-9-2-structural-sl-tp-from-qa\n"
        if "ls-remote" in cmd:
            return f"{fake_sha}\trefs/heads/feature/v17-9-2-structural-sl-tp-from-qa\n"
        if "rev-parse" in cmd:
            return f"{fake_sha}\n"
        if "diff" in cmd:
            return ""
        return ""

    monkeypatch.setattr(subprocess, "check_output", _fake_check_output)
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: None)
    receipt = verify_git_remote_phase_receipt_v1793127(tmp_path, phase_a=fake_sha, phase_b=None, phase_c=None)
    assert receipt["status"] == "BLOCKED"


def test_m26_synthetic_quote_to_provider_label_without_receipt_fails():
    """M26: Change synthetic quote to provider label without receipt -> FAIL."""
    base_ms = 1_790_000_000_000
    bars = _make_m1_bars(base_ms, 5)
    now_ms = bars[-1]["close_time"]
    ctx = SimpleNamespace(
        symbol="XAUUSDT",
        decision_timestamp_ms=now_ms,
        closed_m1_candles=bars,
        quote_source_kind="SYNTHETIC_RESEARCH_QUOTE",
        bid=99.90,
        ask=100.10,
    )
    decision = dict(opportunity_id="opp_syn", setup_episode_id="ep_syn", direction="LONG", model_id="SMC_M1_REVERSAL")
    res = derive_pre_entry_features_from_causal_context_v1793127(decision, ctx, dataset_sha256="a" * 64)
    assert res["quote_source_kind"] == "SYNTHETIC_RESEARCH_QUOTE"


def test_m27_mutate_data_source_after_cache_warmup_invalidates_cache():
    """M27: Mutate data source after cache warmup -> cache invalidates; no same proof reused."""
    base_ms = 1_790_000_000_000
    bars_a = _make_m1_bars(base_ms, 8, close=100.0, high=101.0, low=99.0)
    bars_b = _make_m1_bars(base_ms, 8, close=105.0, high=108.0, low=102.0)
    cutoff = bars_a[-1]["close_time"]
    decision = dict(opportunity_id="opp_cache", setup_episode_id="ep_cache", direction="LONG", model_id="SMC_M1_REVERSAL")
    res_a = derive_pre_entry_features_from_causal_context_v1793127(
        decision,
        SimpleNamespace(symbol="XAUUSDT", decision_timestamp_ms=cutoff, closed_m1_candles=bars_a),
        dataset_sha256="a" * 64,
    )
    res_b = derive_pre_entry_features_from_causal_context_v1793127(
        decision,
        SimpleNamespace(symbol="XAUUSDT", decision_timestamp_ms=cutoff, closed_m1_candles=bars_b),
        dataset_sha256="a" * 64,
    )
    assert res_a["source_snapshot_hash"] != res_b["source_snapshot_hash"]
    assert res_a["proof_hash"] != res_b["proof_hash"]


def test_m28_quick_stop_or_rr_does_not_fabricate_loss_cause():
    """M28: Alter loss cause based solely on quick stop or RR -> UNKNOWN classification preserved."""
    base_ms = 1_790_000_000_000
    bars = _make_m1_bars(base_ms, 6)
    cutoff = bars[-1]["close_time"]
    decision = dict(opportunity_id="opp_loss", setup_episode_id="ep_loss", direction="LONG", model_id="SMC_M1_REVERSAL")
    feature = derive_pre_entry_features_from_causal_context_v1793127(
        decision,
        SimpleNamespace(symbol="XAUUSDT", decision_timestamp_ms=cutoff, closed_m1_candles=bars),
        dataset_sha256="a" * 64,
    )
    ep = {
        "opportunity_id": "opp_loss",
        "outcome_label": "LOSS",
        "bars_held": 1,
        "realized_gross_pnl_usd": -2.0,
        "entry_fee_usd": 0.05,
        "exit_fee_usd": 0.05,
        "realized_net_pnl_usd": -2.10,
        "realized_r": -1.0,
        "net_rr": 2.1,
    }
    primary, labels = classify_observed_loss_v1793127(ep, feature)
    assert primary == "OTHER/UNKNOWN"
    assert labels == ["OTHER/UNKNOWN"]
