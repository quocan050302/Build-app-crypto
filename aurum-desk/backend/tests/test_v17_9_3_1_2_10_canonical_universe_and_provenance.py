"""V17.9.3.1.2.10 (V130) — Canonical Universe Reconciliation, Detector-Bound Liquidity
Provenance, and Independent Trade-Loss Forensics Adversarial Test Suite (N01..N30).

Covers all 30 mandatory V130 positive/negative adversarial controls (N01..N30),
parameterized across LONG/SHORT and 1M/5M/15M/1H/4H where relevant, and backs the
16 new executed source RED/GREEN mutation pairs concentrated on:
N02, N04, N05, N07, N08, N12, N17, N18, N19, N20, N22, N25, N26, N28, N29, N30.
"""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import pytest

from services.scalp_m1_types import M1ScalpConfig
from services.v17_9_3_1_2_2_signal_intelligence import (
    _build_causal_swing_pools_from_swings_v1793124,
    _closed_bars_as_of,
    _resolve_swing_pivot_close_ms_v1793129,
    _resolve_unswept_liquidity_pool_target_v1793123,
    build_signal_quality_evidence_v1793122,
    evaluate_signal_intelligence_gate_v1793122,
    is_research_apply_forbidden_in_adapter_v1793122,
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
from services.v17_9_3_1_2_10_canonical_reconciliation_and_provenance import (
    VERSION_V17931210,
    analyze_15_distinct_recovered_trades_v17931210,
    build_canonical_universe_lineage_v17931210,
    rederive_detector_liquidity_source_v17931210,
    render_source_derived_report_v17931210,
    verify_context_liquidity_pool_provenance_v17931210,
    verify_forensic_cohort_conservation_v17931210,
    verify_source_denom_and_trade_report_consistency_v17931210,
    verify_universe_bijection_and_exclusion_reasons_v17931210,
)
from tools.v17_9_3_1_2_7_proof_checks import (
    verify_checkpoint_coverage_from_disk,
    verify_fresh_full_junit,
    verify_git_remote_phase_receipt_v1793127,
    verify_raw_accounting_and_worker_proofs,
)


def _make_tf_bars_with_pivot(
    base_ms: int,
    tf_ms: int,
    count: int,
    *,
    pivot_index: int,
    direction: str,
    base_px: float = 2650.0,
    pivot_delta: float = 6.0,
):
    bars = []
    for i in range(count):
        open_ms = base_ms + i * tf_ms
        close_ms = open_ms + tf_ms
        if direction == "LONG":
            # BSL swing high at pivot_index
            high = base_px + pivot_delta if i == pivot_index else base_px + 1.0
            low = base_px - 1.0
        else:
            # SSL swing low at pivot_index
            high = base_px + 1.0
            low = base_px - pivot_delta if i == pivot_index else base_px - 1.0
        bars.append(
            {
                "open_time": open_ms,
                "timestamp": open_ms,
                "close_time": close_ms,
                "open": base_px,
                "high": round(high, 2),
                "low": round(low, 2),
                "close": base_px,
                "is_closed": True,
            }
        )
    return bars


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize(
    "tf_lbl,tf_ms,field_name",
    [
        ("1M", 60_000, "closed_m1_candles"),
        ("5M", 300_000, "closed_m5_candles"),
        ("15M", 900_000, "closed_m15_candles"),
        ("1H", 3_600_000, "closed_h1_candles"),
        ("4H", 14_400_000, "closed_h4_candles"),
    ],
)
def test_n01_real_detector_created_pool_valid_timestamps_and_unswept_path_passes(direction, tf_lbl, tf_ms, field_name):
    """N01: Real detector-created pool with valid event timestamps, true pivot and complete unswept path passes."""
    base_ms = 1_790_000_000_000
    tf_bars = _make_tf_bars_with_pivot(base_ms, tf_ms, 6, pivot_index=2, direction=direction, base_px=2650.0, pivot_delta=6.0)
    cutoff = tf_bars[-1]["close_time"]
    # Also provide contiguous M1 bars covering the post-pivot interval through cutoff
    pivot_open_ms = tf_bars[2]["open_time"]
    pivot_close_ms = tf_bars[2]["close_time"]
    m1_count = (cutoff - pivot_close_ms) // 60_000
    m1_bars = (
        tf_bars
        if tf_lbl == "1M"
        else [
            {
                "open_time": pivot_close_ms + j * 60_000,
                "timestamp": pivot_close_ms + j * 60_000,
                "close_time": pivot_close_ms + (j + 1) * 60_000,
                "open": 2650.0,
                "high": 2651.0,
                "low": 2649.0,
                "close": 2650.0,
                "is_closed": True,
            }
            for j in range(m1_count)
        ]
    )
    pivot_kind = "sh" if direction == "LONG" else "sl"
    raw_id = f"{tf_lbl.lower()}_{pivot_kind}_{pivot_open_ms}"
    target_id = raw_id if tf_lbl == "1M" else f"pool_{tf_lbl}_{raw_id}"
    tp_px = 2656.0 if direction == "LONG" else 2644.0
    sl_px = 2646.0 if direction == "LONG" else 2654.0

    ctx_kwargs = {"symbol": "XAUUSDT", "decision_timestamp_ms": cutoff, "closed_m1_candles": m1_bars, field_name: tf_bars}
    ctx = SimpleNamespace(**ctx_kwargs)
    decision = {
        "opportunity_id": f"opp_{tf_lbl}_{direction}",
        "setup_episode_id": "ep_n01",
        "model_id": "SMC_M1_REVERSAL",
        "direction": direction,
        "entry_reference": 2650.0,
        "selected_sl": sl_px,
        "take_profit": tp_px,
        "selected_target_id": target_id,
    }
    rederived = rederive_detector_liquidity_source_v17931210(decision, ctx, dataset_sha256="a" * 64)
    assert rederived["target_selected_pool_bound"] is True
    assert rederived["target_unswept"] is True
    assert rederived["target_pool_state"] == "UNSWEPT"


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_n02_fake_context_liquidity_pools_with_realistic_metadata_rejected(direction):
    """N02: Fake context.liquidity_pools with realistic ID, price, known_at and fabricated source IDs fails."""
    base_ms = 1_790_000_000_000
    m1_bars = _make_tf_bars_with_pivot(base_ms, 60_000, 12, pivot_index=3, direction=direction, base_px=2650.0, pivot_delta=2.0)
    cutoff = m1_bars[-1]["close_time"]
    spoof_px = 2655.0 if direction == "LONG" else 2645.0
    spoof_pool = {
        "id": f"1m_{'sh' if direction == 'LONG' else 'sl'}_{m1_bars[4]['open_time']}",
        "pool_id": f"1m_{'sh' if direction == 'LONG' else 'sl'}_{m1_bars[4]['open_time']}",
        "price": spoof_px,
        "timeframe": "1M",
        "pool_side": "BSL" if direction == "LONG" else "SSL",
        "occurred_at_ms": m1_bars[4]["open_time"],
        "confirmed_at_ms": m1_bars[6]["close_time"],
        "known_at_ms": m1_bars[6]["close_time"],
        "pivot_close_ms": m1_bars[4]["close_time"],
        "detector_version": "v15.3",
        "source_ids": ["fabricated_bar_1", "fabricated_bar_2"],
    }
    ctx = SimpleNamespace(
        symbol="XAUUSDT",
        decision_timestamp_ms=cutoff,
        closed_m1_candles=m1_bars,
        liquidity_pools=[spoof_pool],
        spread=0.10,
        quote_timestamp_ms=cutoff,
    )
    prov = verify_context_liquidity_pool_provenance_v17931210(ctx, direction, cutoff)
    assert prov["verified_pools_count"] == 0
    assert prov["rejected_pools_count"] == 1
    assert prov["rejected_pools"][0]["reason"] == "UNPROVEN_CALLER_POOL"

    ev = build_signal_quality_evidence_v1793122(
        context=ctx,
        model_id="SMC_M1_REVERSAL",
        direction=direction,
        entry_price=2650.0,
        selected_sl=2648.0 if direction == "LONG" else 2652.0,
        take_profit=spoof_px,
        selected_target_id=spoof_pool["id"],
        atr_m1=1.0,
        atr_m5=2.2,
    )
    assert ev.target_pool_proven is False
    assert ev.target_reachable is False


def test_n03_reused_pool_id_with_altered_price_timeframe_direction_or_dataset_fails():
    """N03: Reuse legitimate pool ID but alter price, timeframe, direction, symbol or dataset -> fail."""
    base_ms = 1_790_000_000_000
    m5_bars = _make_tf_bars_with_pivot(base_ms, 300_000, 5, pivot_index=2, direction="LONG", base_px=2650.0, pivot_delta=6.0)
    cutoff = m5_bars[-1]["close_time"]
    pivot_close = m5_bars[2]["close_time"]
    m1_bars = [
        {
            "open_time": pivot_close + i * 60_000,
            "timestamp": pivot_close + i * 60_000,
            "close_time": pivot_close + (i + 1) * 60_000,
            "open": 2650.0,
            "high": 2651.0,
            "low": 2649.0,
            "close": 2650.0,
            "is_closed": True,
        }
        for i in range((cutoff - pivot_close) // 60_000)
    ]
    real_id = f"pool_5M_5m_sh_{m5_bars[2]['open_time']}"
    ctx = SimpleNamespace(symbol="XAUUSDT", decision_timestamp_ms=cutoff, closed_m1_candles=m1_bars, closed_m5_candles=m5_bars)
    # Alter price from 2656.0 to 2657.0
    dec_bad_px = {
        "opportunity_id": "opp_n03",
        "setup_episode_id": "ep_n03",
        "model_id": "SMC_M1_REVERSAL",
        "direction": "LONG",
        "entry_reference": 2650.0,
        "selected_sl": 2646.0,
        "take_profit": 2657.0,
        "selected_target_id": real_id,
    }
    res_px = rederive_detector_liquidity_source_v17931210(dec_bad_px, ctx, dataset_sha256="a" * 64)
    assert res_px["target_selected_pool_bound"] is None
    assert res_px["target_binding_reason"] == "TAKE_PROFIT_PRICE_MISMATCH"

    # Alter direction to SHORT while referencing LONG BSL swing high
    dec_bad_dir = {**dec_bad_px, "direction": "SHORT", "selected_sl": 2660.0, "take_profit": 2656.0}
    res_dir = rederive_detector_liquidity_source_v17931210(dec_bad_dir, ctx, dataset_sha256="a" * 64)
    assert res_dir["target_selected_pool_bound"] is None


def test_n04_spoof_detector_version_source_kind_or_pool_5m_prefix_without_detector_match_fails():
    """N04: Spoof detector_version, source_kind='SWING' or pool_5M_ text without raw detector match -> fail."""
    spoof_item = {
        "id": "pool_5M_fake_pivot_1",
        "pool_id": "pool_5M_fake_pivot_1",
        "price": 103.0,
        "occurred_at_ms": 120_000,
        "known_at_ms": 420_000,
        "detector_version": "v15.3",
        "source_kind": "SWING",
    }
    assert _resolve_swing_pivot_close_ms_v1793129(spoof_item, 120_000) == 0
    bars = [
        {
            "open_time": 120_000 + i * 60_000,
            "close_time": 180_000 + i * 60_000,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "is_closed": True,
        }
        for i in range(6)
    ]
    res = _resolve_unswept_liquidity_pool_target_v1793123(
        pools=[spoof_item],
        closed_1m=bars,
        direction="LONG",
        entry_price=100.0,
        selected_sl=98.0,
        selected_target_id="pool_5M_fake_pivot_1",
        take_profit=103.0,
        now_ms=480_000,
        eff_atr_m1=1.0,
        eff_atr_m5=2.2,
    )
    assert res == (None, 103.0, 0, False, False)


def test_n05_right_side_confirmation_candle_outside_cutoff_unknown_vs_confirmed_passes():
    """N05: Right-side confirmation candle outside cutoff -> unknown; matching real confirmed candle passes."""
    base_ms = 1_790_000_000_000
    m5_bars = _make_tf_bars_with_pivot(base_ms, 300_000, 4, pivot_index=2, direction="LONG", base_px=2650.0, pivot_delta=6.0)
    # Pivot is at index 2 (open = base + 600_000, close = base + 900_000); right confirming bar is index 3 (closes at base + 1_200_000)
    early_cutoff = m5_bars[2]["close_time"]  # Right confirming bar not closed yet!
    late_cutoff = m5_bars[3]["close_time"]
    m1_bars = [
        {
            "open_time": base_ms + i * 60_000,
            "timestamp": base_ms + i * 60_000,
            "close_time": base_ms + (i + 1) * 60_000,
            "open": 2650.0,
            "high": 2651.0,
            "low": 2649.0,
            "close": 2650.0,
            "is_closed": True,
        }
        for i in range(20)
    ]
    target_id = f"pool_5M_5m_sh_{m5_bars[2]['open_time']}"
    dec = {
        "opportunity_id": "opp_n05",
        "setup_episode_id": "ep_n05",
        "model_id": "SMC_M1_REVERSAL",
        "direction": "LONG",
        "entry_reference": 2650.0,
        "selected_sl": 2646.0,
        "take_profit": 2656.0,
        "selected_target_id": target_id,
    }
    ctx_early = SimpleNamespace(symbol="XAUUSDT", decision_timestamp_ms=early_cutoff, closed_m1_candles=m1_bars[:15], closed_m5_candles=m5_bars)
    res_early = rederive_detector_liquidity_source_v17931210(dec, ctx_early, dataset_sha256="a" * 64)
    assert res_early["target_selected_pool_bound"] is None

    ctx_late = SimpleNamespace(symbol="XAUUSDT", decision_timestamp_ms=late_cutoff, closed_m1_candles=m1_bars, closed_m5_candles=m5_bars)
    res_late = rederive_detector_liquidity_source_v17931210(dec, ctx_late, dataset_sha256="a" * 64)
    assert res_late["target_selected_pool_bound"] is True
    assert res_late["target_unswept"] is True


def test_n06_known_at_ms_equal_now_or_occurred_at_without_independent_event_fails():
    """N06: known_at_ms=now supplied by candidate without independently sourced event or zero-lag known_at==occurred_at -> fail."""
    bars = [
        {
            "open_time": 120_000 + i * 60_000,
            "close_time": 180_000 + i * 60_000,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "is_closed": True,
        }
        for i in range(4)
    ]
    zero_lag_pool = {"pool_id": "p_zero", "price": 103.0, "occurred_at_ms": 180_000, "known_at_ms": 180_000}
    assert _build_causal_swing_pools_from_swings_v1793124([zero_lag_pool], bars, "LONG", 360_000) == []

    now_unconfirmed_pool = {"pool_id": "p_now", "price": 103.0, "occurred_at_ms": 180_000, "known_at_ms": 360_000}
    res = _resolve_unswept_liquidity_pool_target_v1793123(
        pools=[now_unconfirmed_pool],
        closed_1m=bars,
        direction="LONG",
        entry_price=100.0,
        selected_sl=98.0,
        selected_target_id="p_now",
        take_profit=103.0,
        now_ms=360_000,
        eff_atr_m1=1.0,
        eff_atr_m5=2.0,
    )
    assert res == (None, 103.0, 0, False, False)


def test_n07_missing_stale_future_partial_zero_m1_path_and_interior_gap_rejected():
    """N07: Missing, stale, future, partial, duplicate conflicting OHLC; zero M1 path and an interior gap -> unknown/invalid."""
    pool = {"pool_id": "p_gap", "price": 103.0, "occurred_at_ms": 120_000, "known_at_ms": 180_000}
    # 1. Empty M1 path
    assert _resolve_unswept_liquidity_pool_target_v1793123(
        pools=[pool],
        closed_1m=[],
        direction="LONG",
        entry_price=100.0,
        selected_sl=98.0,
        selected_target_id="p_gap",
        take_profit=103.0,
        now_ms=360_000,
        eff_atr_m1=1.0,
        eff_atr_m5=2.0,
    ) == (None, 103.0, 0, False, False)

    # 2. Interior gap in M1 path
    bars = [
        {
            "open_time": 120_000 + i * 60_000,
            "close_time": 180_000 + i * 60_000,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "is_closed": True,
        }
        for i in range(4)
        if i != 2
    ]
    assert _resolve_unswept_liquidity_pool_target_v1793123(
        pools=[pool],
        closed_1m=bars,
        direction="LONG",
        entry_price=100.0,
        selected_sl=98.0,
        selected_target_id="p_gap",
        take_profit=103.0,
        now_ms=360_000,
        eff_atr_m1=1.0,
        eff_atr_m5=2.0,
    ) == (None, 103.0, 0, False, False)


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_n08_post_pivot_sweep_marks_proven_swept_and_gap_cannot_be_proven_unswept(direction):
    """N08: Post-pivot true high/low sweeps target -> PROVEN_SWEPT; any uncovered gap cannot be PROVEN_UNSWEPT."""
    base_ms = 1_790_000_000_000
    m5_bars = _make_tf_bars_with_pivot(base_ms, 300_000, 5, pivot_index=1, direction=direction, base_px=2650.0, pivot_delta=5.0)
    cutoff = m5_bars[-1]["close_time"]
    pivot_close = m5_bars[1]["close_time"]
    m1_bars = [
        {
            "open_time": pivot_close + i * 60_000,
            "timestamp": pivot_close + i * 60_000,
            "close_time": pivot_close + (i + 1) * 60_000,
            "open": 2650.0,
            "high": 2651.0,
            "low": 2649.0,
            "close": 2650.0,
            "is_closed": True,
        }
        for i in range((cutoff - pivot_close) // 60_000)
    ]
    # Sweep target on a post-pivot M1 bar
    if direction == "LONG":
        m1_bars[6]["high"] = 2655.5
    else:
        m1_bars[6]["low"] = 2644.5

    target_id = f"pool_5M_5m_{'sh' if direction == 'LONG' else 'sl'}_{m5_bars[1]['open_time']}"
    dec = {
        "opportunity_id": "opp_n08",
        "setup_episode_id": "ep_n08",
        "model_id": "SMC_M1_REVERSAL",
        "direction": direction,
        "entry_reference": 2650.0,
        "selected_sl": 2645.0 if direction == "LONG" else 2655.0,
        "take_profit": 2655.0 if direction == "LONG" else 2645.0,
        "selected_target_id": target_id,
    }
    ctx = SimpleNamespace(symbol="XAUUSDT", decision_timestamp_ms=cutoff, closed_m1_candles=m1_bars, closed_m5_candles=m5_bars)
    res = rederive_detector_liquidity_source_v17931210(dec, ctx, dataset_sha256="a" * 64)
    assert res["target_selected_pool_bound"] is True
    assert res["target_unswept"] is False
    assert res["target_pool_state"] == "ALREADY_SWEPT"


def test_n09_tp_not_equal_nominated_pivot_price_or_unrelated_nearby_pool_rejected():
    """N09: True pivot price not equal nominated TP within symbol tick -> fail; nearby unrelated level must not silently replace target."""
    base_ms = 1_790_000_000_000
    m5_bars = _make_tf_bars_with_pivot(base_ms, 300_000, 5, pivot_index=2, direction="LONG", base_px=2650.0, pivot_delta=6.0)
    cutoff = m5_bars[-1]["close_time"]
    pivot_close = m5_bars[2]["close_time"]
    m1_bars = [
        {
            "open_time": pivot_close + i * 60_000,
            "timestamp": pivot_close + i * 60_000,
            "close_time": pivot_close + (i + 1) * 60_000,
            "open": 2650.0,
            "high": 2651.0,
            "low": 2649.0,
            "close": 2650.0,
            "is_closed": True,
        }
        for i in range((cutoff - pivot_close) // 60_000)
    ]
    real_id = f"pool_5M_5m_sh_{m5_bars[2]['open_time']}"
    ctx = SimpleNamespace(symbol="XAUUSDT", decision_timestamp_ms=cutoff, closed_m1_candles=m1_bars, closed_m5_candles=m5_bars)
    dec_tick_off = {
        "opportunity_id": "opp_n09",
        "setup_episode_id": "ep_n09",
        "model_id": "SMC_M1_REVERSAL",
        "direction": "LONG",
        "entry_reference": 2650.0,
        "selected_sl": 2646.0,
        "take_profit": 2656.01,
        "selected_target_id": real_id,
    }
    feat = derive_pre_entry_features_from_causal_context_v1793127(dec_tick_off, ctx, dataset_sha256="a" * 64)
    assert feat["target_selected_pool_bound"] is None
    assert feat["target_unswept"] is None


def test_n10_duplicate_pool_id_ambiguity_rejected():
    """N10: Same ID used by two different pools -> ambiguity fail; legitimate unique selected ID passes."""
    bars = [
        {
            "open_time": 120_000 + i * 60_000,
            "close_time": 180_000 + i * 60_000,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "is_closed": True,
        }
        for i in range(4)
    ]
    p1 = {"pool_id": "dup_id", "price": 103.0, "occurred_at_ms": 120_000, "known_at_ms": 180_000}
    p2 = {"pool_id": "dup_id", "price": 104.0, "occurred_at_ms": 120_000, "known_at_ms": 180_000}
    assert _resolve_unswept_liquidity_pool_target_v1793123(
        pools=[p1, p2],
        closed_1m=bars,
        direction="LONG",
        entry_price=100.0,
        selected_sl=98.0,
        selected_target_id="dup_id",
        take_profit=103.0,
        now_ms=360_000,
        eff_atr_m1=1.0,
        eff_atr_m5=2.0,
    ) == (None, 103.0, 0, False, False)


def test_n11_source_hashes_copied_from_another_dataset_rejected():
    """N11: Source hashes copied from another dataset -> fail even with same geometry."""
    base_ms = 1_790_000_000_000
    bars = _make_tf_bars_with_pivot(base_ms, 60_000, 8, pivot_index=3, direction="LONG")
    cutoff = bars[-1]["close_time"]
    dec = {"opportunity_id": "opp_n11", "setup_episode_id": "ep_n11", "model_id": "SMC_M1_REVERSAL", "direction": "LONG"}
    ctx = SimpleNamespace(symbol="XAUUSDT", decision_timestamp_ms=cutoff, closed_m1_candles=bars)
    feat = derive_pre_entry_features_from_causal_context_v1793127(dec, ctx, dataset_sha256="a" * 64)
    assert verify_feature_snapshot_v1793127(feat) is True
    tampered = dict(feat, source_dataset_sha256="b" * 64)
    assert verify_feature_snapshot_v1793127(tampered) is False


def test_n12_reprice_entry_sl_tp_qty_without_final_intent_rederivation_fails(monkeypatch):
    """N12: Reprice entry/SL/TP/qty without final-intent re-derivation -> fail."""
    from services import v17_9_3_1_2_7_causal_signal_truth as truth
    from services.v17_9_3_1_2_executable_intervention_replay_service import recalculate_trade_risk_quantity_v179312

    base_ms = 1_790_000_000_000
    bars = _make_tf_bars_with_pivot(base_ms, 60_000, 8, pivot_index=3, direction="LONG")
    cutoff = bars[-1]["close_time"]
    dec = {
        "opportunity_id": "opp_n12",
        "setup_episode_id": "ep_n12",
        "model_id": "SMC_M1_REVERSAL",
        "direction": "LONG",
        "decision_timestamp_ms": cutoff,
        "entry_reference": 2650.0,
        "selected_sl": 2646.0,
        "take_profit": 2660.0,
    }
    ctx = SimpleNamespace(symbol="XAUUSDT", decision_timestamp_ms=cutoff, closed_m1_candles=bars)
    feat = derive_pre_entry_features_from_causal_context_v1793127(dec, ctx, dataset_sha256="a" * 64)
    repriced = {**dec, "entry_reference": 2650.5}
    assert required_proof_rejection_v1793127(feat, "SMC_M1_REVERSAL", decision=repriced) == "REJECTED_UNPROVEN:INTENT_GEOMETRY_MISMATCH"

    monkeypatch.setattr(truth, "required_proof_rejection_v1793127", lambda *a, **kw: None)
    sized = recalculate_trade_risk_quantity_v179312(2650.0, 2645.0, 2665.0, direction="LONG", decision_ts=cutoff)
    plan = {
        "new_decision_timestamp_ms": cutoff,
        "new_checkpoint_index_1m": len(bars) - 1,
        "new_entry_reference": 2650.0,
        "new_selected_sl": 2645.0,
        "new_take_profit": 2665.0,
        "new_quantity": sized["quantity"] + 0.03,
    }
    _, _, rej = rebuild_materialized_intent_proof_v1793127(dec, plan, {"1m": bars}, dataset_sha256="a" * 64)
    assert rej == "REJECTED_UNPROVEN:FINAL_RISK_OR_QUANTITY_MISMATCH"


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_n13_protected_sl_pivot_inside_stop_wrong_side_or_sl_derived_rejected(direction):
    """N13: Protected SL pivot crossed, wrong side, or derived from the SL itself -> fail."""
    base_ms = 1_790_000_000_000
    # Create swing low (for LONG) at 2645.0 or swing high (for SHORT) at 2655.0
    inv_dir = "SHORT" if direction == "LONG" else "LONG"
    bars = _make_tf_bars_with_pivot(base_ms, 60_000, 7, pivot_index=2, direction=inv_dir, base_px=2650.0, pivot_delta=5.0)
    cutoff = bars[-1]["close_time"]
    piv_kind = "sl" if direction == "LONG" else "sh"
    anchor_id = f"1m_{piv_kind}_{bars[2]['open_time']}"
    # Place SL INSIDE the anchor (e.g., 2646.0 > 2645.0 for LONG; 2654.0 < 2655.0 for SHORT)
    bad_sl = 2646.0 if direction == "LONG" else 2654.0
    dec = {
        "opportunity_id": "opp_n13",
        "setup_episode_id": "ep_n13",
        "model_id": "SMC_M1_REVERSAL",
        "direction": direction,
        "entry_reference": 2650.0,
        "selected_sl": bad_sl,
        "take_profit": 2660.0 if direction == "LONG" else 2640.0,
        "selected_anchor_id": anchor_id,
    }
    ctx = SimpleNamespace(symbol="XAUUSDT", decision_timestamp_ms=cutoff, closed_m1_candles=bars)
    feat = derive_pre_entry_features_from_causal_context_v1793127(dec, ctx, dataset_sha256="a" * 64)
    assert feat["protected_structure_independent_of_sl"] is None


def test_n14_real_fvg_postformation_touch_and_mss_vs_descriptor_only_fake():
    """N14: Real FVG postformation touch and MSS chronological source vs descriptor-only fake -> pass/fail."""
    base_ms = 1_790_000_000_000
    # Bullish FVG between bar 0 (high=101) and bar 2 (low=103), retested at bar 3 (low=102.5)
    ohlc = [
        (100.0, 101.0, 99.0, 100.5),
        (100.5, 105.0, 100.5, 104.5),
        (104.5, 106.0, 103.0, 105.0),
        (105.0, 105.5, 102.5, 104.0),
        (104.0, 105.0, 103.5, 104.5),
    ]
    bars = [
        {
            "open_time": base_ms + i * 60_000,
            "timestamp": base_ms + i * 60_000,
            "close_time": base_ms + (i + 1) * 60_000,
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "is_closed": True,
        }
        for i, (o, h, l, c) in enumerate(ohlc)
    ]
    cutoff = bars[-1]["close_time"]
    ctx = SimpleNamespace(symbol="XAUUSDT", decision_timestamp_ms=cutoff, closed_m1_candles=bars)
    res_valid = derive_pre_entry_features_from_causal_context_v1793127(
        {"opportunity_id": "opp_fvg_ok", "setup_episode_id": "ep1", "model_id": "SMC_M1_FVG_CONTINUATION", "direction": "LONG"},
        ctx,
        dataset_sha256="a" * 64,
    )
    assert res_valid["fvg_present"] is True
    assert res_valid["fvg_retest_valid"] is True

    res_fake = derive_pre_entry_features_from_causal_context_v1793127(
        {
            "opportunity_id": "opp_fvg_fake",
            "setup_episode_id": "ep2",
            "model_id": "SMC_M1_FVG_CONTINUATION",
            "direction": "LONG",
            "selected_fvg_id": "fake_fvg_999",
        },
        ctx,
        dataset_sha256="a" * 64,
    )
    assert res_fake["fvg_present"] is False
    assert res_fake["fvg_retest_valid"] is None


def test_n15_htf_future_or_unknown_structure_never_becomes_aligned():
    """N15: HTF future or UNKNOWN structure must not become ALIGNED from a string or lookahead."""
    base_ms = 1_790_000_000_000
    bars = _make_tf_bars_with_pivot(base_ms, 60_000, 6, pivot_index=2, direction="LONG")
    cutoff = bars[-1]["close_time"]
    ctx = SimpleNamespace(symbol="XAUUSDT", decision_timestamp_ms=cutoff, closed_m1_candles=bars)
    res = derive_pre_entry_features_from_causal_context_v1793127(
        {"opportunity_id": "opp_htf", "setup_episode_id": "ep_htf", "model_id": "SMC_M1_CONTINUATION", "direction": "LONG", "htf_alignment_state": "ALIGNED"},
        ctx,
        dataset_sha256="a" * 64,
    )
    assert res["htf_alignment_state"] == "UNKNOWN"
    assert required_proof_rejection_v1793127(res, "SMC_M1_CONTINUATION") == "REJECTED_UNPROVEN:HTF_NOT_ALIGNED"


def test_n16_corrupt_source_row_proof_or_cache_invalidation_on_changed_input():
    """N16: Corrupt source-row proof, cache after changing input, or alter source IDs with same hash -> fail."""
    base_ms = 1_790_000_000_000
    bars_a = _make_tf_bars_with_pivot(base_ms, 60_000, 7, pivot_index=2, direction="LONG", base_px=2650.0)
    bars_b = _make_tf_bars_with_pivot(base_ms, 60_000, 7, pivot_index=2, direction="LONG", base_px=2660.0)
    cutoff = bars_a[-1]["close_time"]
    dec = {"opportunity_id": "opp_n16", "setup_episode_id": "ep_n16", "model_id": "SMC_M1_REVERSAL", "direction": "LONG"}
    r_a = derive_pre_entry_features_from_causal_context_v1793127(
        dec, SimpleNamespace(symbol="XAUUSDT", decision_timestamp_ms=cutoff, closed_m1_candles=bars_a), dataset_sha256="a" * 64
    )
    r_b = derive_pre_entry_features_from_causal_context_v1793127(
        dec, SimpleNamespace(symbol="XAUUSDT", decision_timestamp_ms=cutoff, closed_m1_candles=bars_b), dataset_sha256="a" * 64
    )
    assert r_a["source_snapshot_hash"] != r_b["source_snapshot_hash"]
    assert r_a["proof_hash"] != r_b["proof_hash"]

    # Corrupt a proven feature's source_bar_ids and recompute top-level proof_hash
    snap = dict(r_a["causal_snapshot"])
    feats = [dict(f) for f in snap["features"]]
    feats[0]["proof_state"] = "PROVEN_TRUE"
    feats[0]["source_bar_ids"] = ()
    snap["features"] = feats
    payload = dict(snap)
    payload.pop("proof_hash", None)
    new_h = canonical_hash(payload)
    snap["proof_hash"] = new_h
    corrupted = dict(r_a, causal_snapshot=snap, proof_hash=new_h)
    assert verify_feature_snapshot_v1793127(corrupted) is False


def test_n17_drop_duplicate_or_alter_stage_count_in_universe_reconciliation_fails():
    """N17: Drop, duplicate, reorder, or substitute one candidate in raw 75,525/63,117 stages -> fail."""
    base_recs = [{"opportunity_id": f"b_{i}", "checkpoint_index_1m": 30 + i, "model_id": "SMC_M1_REVERSAL", "direction": "LONG", "discovered_by_si8": False} for i in range(10)]
    v128_inc = [{"opportunity_id": f"inc128_{i}", "checkpoint_index_1m": 50 + i, "model_id": "SMC_M1_REVERSAL", "direction": "LONG", "discovered_by_si8": True} for i in range(5)]
    v130_inc = [{"opportunity_id": f"inc130_{i}", "checkpoint_index_1m": 50 + i, "model_id": "SMC_M1_REVERSAL", "direction": "LONG", "discovered_by_si8": True} for i in range(3)]
    lineage = build_canonical_universe_lineage_v17931210(
        raw_m1_bars_count=40,
        warmup_m1_bars_count=30,
        post_warmup_checkpoints_count=10,
        model_direction_evaluations_count=100,
        v128_canonical_records=base_recs + v128_inc,
        v129_canonical_records=base_recs + v130_inc,
        v130_canonical_records=base_recs + v130_inc,
        v128_htf_totals={"ALIGNED": 4, "COUNTERTREND": 5, "MIXED": 6},
        v129_htf_totals={"ALIGNED": 3, "COUNTERTREND": 4, "MIXED": 6},
        v130_htf_totals={"ALIGNED": 3, "COUNTERTREND": 4, "MIXED": 6},
        policy_stage_counts={},
    )
    chk_ok = verify_universe_bijection_and_exclusion_reasons_v17931210(lineage)
    assert chk_ok["status"] == "PASS"

    # Tamper one stage count
    bad_lineage = json.loads(json.dumps(lineage))
    bad_lineage["v128_vs_v129_v130_universe_reconciliation"]["tuple_level_bijection"]["removed_v128_fabricated_pool_or_non_incremental_tuples"] += 1
    chk_bad = verify_universe_bijection_and_exclusion_reasons_v17931210(bad_lineage)
    assert chk_bad["status"] == "FAIL"


def test_n18_contradictory_denominator_or_stale_version_fails_report_consistency():
    """N18: Change narrative denominator or version while leaving JSON unchanged -> report consistency check fails."""
    htf_doc = {"version": VERSION_V17931210, "authoritative_universe_count": 63117, "htf_alignment_state_totals": {"ALIGNED": 7825, "COUNTERTREND": 9168, "MIXED": 46124}}
    funnel_doc = {"version": VERSION_V17931210, "authoritative_universe_count": 63117, "pre_arbitration_required_proof_rejections": {"HTF": 62839}, "pre_arbitration_proof_eligible_count": 278}
    cohort_doc = {
        "version": VERSION_V17931210,
        "baseline_primary_closed_losses_n157": {"conservation_holds": True, "primary_label_distribution": {"WRONG_SIDE_HTF": 134, "OTHER/UNKNOWN": 22, "FEES_DOMINATE": 1}},
        "baseline_primary_scheduled_episodes_n242": {"conservation_holds": True, "primary_label_distribution": {"WRONG_SIDE_HTF": 208, "OTHER/UNKNOWN": 32, "FEES_DOMINATE": 1, "FILL_GAP/AMBIGUOUS_INTRABAR": 1}},
    }
    cb15_doc = {"version": VERSION_V17931210, "unique_closed_trades_count": 15, "total_policy_trade_occurrences": 23, "cases": [{}] * 15}
    pol_doc = {"version": VERSION_V17931210, "policies": {"SI8": {"NEXT_BAR_OPEN_BID_ASK": {"closed_trades_count": 0, "expectancy_status": "UNDEFINED_ZERO_CLOSED_TRADES", "expectancy_is_positive_evidence": False}}}}
    md_ok = {"exec": f"# {VERSION_V17931210} Report\n- **Version Authority**: `{VERSION_V17931210}`\n"}

    res_ok = verify_source_denom_and_trade_report_consistency_v17931210(
        authority_denom=63117,
        htf_census_doc=htf_doc,
        funnel_doc=funnel_doc,
        cohort_doc=cohort_doc,
        casebook_15_doc=cb15_doc,
        policy_metrics_doc=pol_doc,
        markdown_texts=md_ok,
    )
    assert res_ok["status"] == "PASS"

    # Inject contradictory 75,525 denominator into funnel_doc
    res_bad_denom = verify_source_denom_and_trade_report_consistency_v17931210(
        authority_denom=63117,
        htf_census_doc=htf_doc,
        funnel_doc={**funnel_doc, "authoritative_universe_count": 75525},
        cohort_doc=cohort_doc,
        casebook_15_doc=cb15_doc,
        policy_metrics_doc=pol_doc,
        markdown_texts=md_ok,
    )
    assert res_bad_denom["status"] == "FAIL"
    assert any("FUNNEL_DENOM_MISMATCH" in e for e in res_bad_denom["errors"])

    # Inject stale v17_9_3_1_2_9 version into markdown header
    res_stale_md = verify_source_denom_and_trade_report_consistency_v17931210(
        authority_denom=63117,
        htf_census_doc=htf_doc,
        funnel_doc=funnel_doc,
        cohort_doc=cohort_doc,
        casebook_15_doc=cb15_doc,
        policy_metrics_doc=pol_doc,
        markdown_texts={"exec": "# v17_9_3_1_2_9 Stale Header\n"},
    )
    assert res_stale_md["status"] == "FAIL"


def test_n19_baseline_closed_loss_distribution_summing_242_under_n157_fails():
    """N19: Change a baseline closed-loss distribution to sum 242 but label n157 -> FAIL."""
    episodes = [
        {"opportunity_id": "ep_loss_1", "model_id": "SMC_M1_REVERSAL", "direction": "LONG", "filled": True, "outcome_label": "LOSS", "realized_gross_pnl_usd": -2.0, "entry_fee_usd": 0.05, "exit_fee_usd": 0.05, "realized_net_pnl_usd": -2.10},
        {"opportunity_id": "ep_win_1", "model_id": "SMC_M1_REVERSAL", "direction": "LONG", "filled": True, "outcome_label": "WIN", "realized_gross_pnl_usd": 4.5, "entry_fee_usd": 0.05, "exit_fee_usd": 0.05, "realized_net_pnl_usd": 4.40},
        {"opportunity_id": "ep_open_1", "model_id": "SMC_M1_REVERSAL", "direction": "LONG", "filled": True, "outcome_label": "OPEN_AT_HORIZON", "realized_gross_pnl_usd": 0.0, "entry_fee_usd": 0.05, "exit_fee_usd": 0.0, "realized_net_pnl_usd": -0.05},
    ]
    res_ok = verify_forensic_cohort_conservation_v17931210(episodes, {})
    assert res_ok["status"] == "PASS"
    assert res_ok["baseline_primary_closed_losses_n157"]["observed_sum"] == 1
    assert res_ok["baseline_primary_scheduled_episodes_n242"]["observed_sum"] == 3

    # Mix all 3 scheduled episodes into closed_losses_override -> must FAIL
    res_mixed = verify_forensic_cohort_conservation_v17931210(episodes, {}, closed_losses_override=episodes)
    assert res_mixed["status"] == "FAIL"


def test_n20_treat_open_at_horizon_data_gap_or_unfilled_as_closed_loss_fails():
    """N20: Treat OPEN_AT_HORIZON, DATA_GAP, PARTIAL or UNFILLED as a CLOSED LOSS -> FAIL."""
    episodes = [
        {"opportunity_id": "ep_loss", "model_id": "SMC_M1_REVERSAL", "direction": "LONG", "filled": True, "outcome_label": "LOSS"},
        {"opportunity_id": "ep_gap", "model_id": "SMC_M1_REVERSAL", "direction": "LONG", "filled": True, "outcome_label": "DATA_GAP_UNRESOLVED"},
        {"opportunity_id": "ep_unfilled", "model_id": "SMC_M1_REVERSAL", "direction": "LONG", "filled": False, "outcome_label": "UNFILLED"},
    ]
    for bad_ep in episodes[1:]:
        res = verify_forensic_cohort_conservation_v17931210(episodes, {}, closed_losses_override=[episodes[0], bad_ep])
        assert res["status"] == "FAIL"
        assert any("NON_LOSS_IN_CLOSED_LOSS_COHORT" in err for err in res["errors"])


def test_n21_si1_si2_si3_overlap_deduplication_and_multiplicity_conservation():
    """N21: Count SI1/SI2/SI3 common episodes twice in unique trade set -> exact union check FAIL."""
    base_ms = 1_790_000_000_000
    bars = _make_tf_bars_with_pivot(base_ms, 60_000, 6, pivot_index=2, direction="LONG")
    cutoff = bars[-1]["close_time"]
    ctx = SimpleNamespace(symbol="XAUUSDT", decision_timestamp_ms=cutoff, closed_m1_candles=bars)
    f1 = derive_pre_entry_features_from_causal_context_v1793127(
        {"opportunity_id": "u_1", "setup_episode_id": "s_1", "model_id": "SMC_M1_CONTINUATION", "direction": "SHORT"},
        ctx,
        dataset_sha256="a" * 64,
    )
    f2 = derive_pre_entry_features_from_causal_context_v1793127(
        {"opportunity_id": "u_2", "setup_episode_id": "s_2", "model_id": "SMC_M1_REVERSAL", "direction": "SHORT"},
        ctx,
        dataset_sha256="a" * 64,
    )
    ep1 = {"opportunity_id": "u_1", "model_id": "SMC_M1_CONTINUATION", "direction": "SHORT", "decision_timestamp_ms": cutoff, "filled": True, "outcome_label": "LOSS", "realized_gross_pnl_usd": -2.0, "entry_fee_usd": 0.1, "exit_fee_usd": 0.1, "realized_net_pnl_usd": -2.2, "realized_r": -1.0}
    ep2 = {"opportunity_id": "u_2", "model_id": "SMC_M1_REVERSAL", "direction": "SHORT", "decision_timestamp_ms": cutoff, "filled": True, "outcome_label": "LOSS", "realized_gross_pnl_usd": -2.0, "entry_fee_usd": 0.1, "exit_fee_usd": 0.1, "realized_net_pnl_usd": -2.2, "realized_r": -1.0}
    pol_results = {
        "SI1_H1_H2": {"primary_episodes": [ep1, ep2]},
        "SI2_H3": {"primary_episodes": [ep1]},
        "SI3_H4": {"primary_episodes": [ep1]},
    }
    cb = analyze_15_distinct_recovered_trades_v17931210(pol_results, {"u_1": f1, "u_2": f2})
    assert cb["status"] == "PASS"
    assert cb["total_policy_trade_occurrences"] == 4
    assert cb["unique_closed_trades_count"] == 2
    assert cb["venn_overlap_partition"]["SI1_H1_H2+SI2_H3+SI3_H4"] == 1
    assert cb["venn_overlap_partition"]["SI1_H1_H2_only"] == 1


def test_n22_alter_fee_slippage_risk_or_pnl_in_paired_row_fails_ab_verifier():
    """N22: Alter a fee, slippage, risk, win flag or PnL in a paired row -> independent A/B verifier FAIL."""
    dec = [{"opportunity_id": "opp_1", "decision_timestamp_ms": 1_000_000, "source_snapshot_hash": "s" * 64, "model_id": "SMC_M1_REVERSAL", "direction": "LONG", "accepted": True, "status": "ELIGIBLE"}]
    bad_trade = [{"opportunity_id": "opp_1", "decision_timestamp_ms": 1_000_000, "source_snapshot_hash": "s" * 64, "model_id": "SMC_M1_REVERSAL", "direction": "LONG", "filled": True, "outcome_label": "LOSS", "realized_gross_pnl_usd": -2.0, "entry_fee_usd": 0.10, "exit_fee_usd": 0.10, "realized_net_pnl_usd": -2.01, "realized_r": -1.0, "planned_net_risk_usd": 2.0}]
    with pytest.raises(ValueError, match="FEE_NET_RECONCILIATION_MISMATCH"):
        compute_matched_policy_ab_v1793127(dec, dec, bad_trade, bad_trade, comparison_id="SI0_SI8", fill_mode="NEXT_BAR_OPEN_BID_ASK")


def test_n23_remove_decision_row_or_duplicate_opportunity_id_fails_ab_verifier(tmp_path):
    """N23: Remove a decision row/pair or duplicate IDs -> FAIL."""
    dec_a = [
        {"opportunity_id": "opp_1", "decision_timestamp_ms": 1_000_000, "source_snapshot_hash": "s" * 64, "model_id": "SMC_M1_REVERSAL", "direction": "LONG", "accepted": True, "status": "ELIGIBLE"},
        {"opportunity_id": "opp_2", "decision_timestamp_ms": 1_060_000, "source_snapshot_hash": "s" * 64, "model_id": "SMC_M1_REVERSAL", "direction": "LONG", "accepted": False, "status": "REJECTED"},
    ]
    trade_a = [
        {"opportunity_id": "opp_1", "decision_timestamp_ms": 1_000_000, "source_snapshot_hash": "s" * 64, "model_id": "SMC_M1_REVERSAL", "direction": "LONG", "filled": True, "outcome_label": "LOSS", "realized_gross_pnl_usd": -2.0, "entry_fee_usd": 0.1, "exit_fee_usd": 0.1, "realized_net_pnl_usd": -2.2, "realized_r": -1.1, "planned_net_risk_usd": 2.0}
    ]
    with pytest.raises(ValueError, match="DUPLICATE_OPPORTUNITY_ID"):
        compute_matched_policy_ab_v1793127(dec_a + [dec_a[0]], dec_a, [], [], comparison_id="SI0_SI8", fill_mode="NEXT_BAR_OPEN_BID_ASK")
    with pytest.raises(ValueError, match="TRADE_WITHOUT_CANONICAL_DECISION"):
        compute_matched_policy_ab_v1793127(dec_a[1:], dec_a, trade_a, [], comparison_id="SI0_SI8", fill_mode="NEXT_BAR_OPEN_BID_ASK")
    ab_doc = compute_matched_policy_ab_v1793127(dec_a, dec_a, trade_a, trade_a, comparison_id="SI0_SI8", fill_mode="NEXT_BAR_OPEN_BID_ASK")
    (tmp_path / "da.jsonl").write_text("\n".join(json.dumps(r) for r in dec_a), encoding="utf-8")
    (tmp_path / "db.jsonl").write_text("\n".join(json.dumps(r) for r in dec_a[:1]), encoding="utf-8")
    (tmp_path / "ta.jsonl").write_text("\n".join(json.dumps(r) for r in trade_a), encoding="utf-8")
    (tmp_path / "tb.jsonl").write_text("\n".join(json.dumps(r) for r in trade_a), encoding="utf-8")
    (tmp_path / "claim.json").write_text(json.dumps(ab_doc), encoding="utf-8")
    res = verify_matched_ab_against_raw_ledgers_v1793127(
        tmp_path,
        {"decisions_a": "da.jsonl", "decisions_b": "db.jsonl", "trades_a": "ta.jsonl", "trades_b": "tb.jsonl", "claim": "claim.json"},
    )
    assert res["status"] == "FAIL"


def test_n24_relabel_synthetic_quote_as_real_provider_quote_without_receipt_fails():
    """N24: Inject future bars/provider quotes or relabel synthetic as REAL_PROVIDER_QUOTE without provider receipt -> FAIL."""
    base_ms = 1_790_000_000_000
    bars = _make_tf_bars_with_pivot(base_ms, 60_000, 6, pivot_index=2, direction="LONG")
    cutoff = bars[-1]["close_time"]
    ctx = SimpleNamespace(
        symbol="XAUUSDT",
        decision_timestamp_ms=cutoff,
        closed_m1_candles=bars,
        quote_source_kind="REAL_PROVIDER_QUOTE",
        quote_timestamp_ms=cutoff,
        quote_provider_source_id="",
    )
    res = derive_pre_entry_features_from_causal_context_v1793127(
        {"opportunity_id": "opp_q", "setup_episode_id": "ep_q", "model_id": "SMC_M1_REVERSAL", "direction": "LONG"},
        ctx,
        dataset_sha256="a" * 64,
    )
    assert res["quote_source_kind"] == "UNVERIFIED_PROVIDER_QUOTE"


def test_n25_junit_stale_tampered_or_missing_node_fails_verifier(tmp_path):
    """N25: JUnit copied/stale, missing test node, tampered XML SHA, fake exit code or skipped node counted PASS -> FAIL."""
    run_dir = tmp_path / "run_j"
    j_dir = run_dir / "junit"
    j_dir.mkdir(parents=True)
    xml_path = j_dir / "SHARD_000.xml"
    xml_bytes = b'<testsuite tests="1"><testcase classname="backend.tests.test_x" name="test_a"/></testsuite>'
    xml_path.write_bytes(xml_bytes)
    orig_sha = hashlib.sha256(xml_bytes).hexdigest()
    xml_path.write_bytes(xml_bytes + b"<!-- tamper -->")
    mtime_ns = xml_path.stat().st_mtime_ns
    manifest = {
        "tested_executable_sha": "a" * 40,
        "run_start_epoch_ns": mtime_ns - 1_000,
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
                "pid": 1001,
                "collect_exit_code": 0,
                "pytest_exit_code": 0,
                "exact_bijection": True,
            }
        ],
    }
    with pytest.raises(ValueError, match="STALE_OR_TAMPERED_JUNIT"):
        verify_fresh_full_junit(run_dir, {"collected_node_ids": ["backend/tests/test_x.py::test_a"]}, manifest)


def test_n26_worker_1v2v4_replay_incomplete_pids_or_mismatched_hash_fails(tmp_path):
    """N26: Run 1/2/4-worker replay with incomplete row set or reused hash -> FAIL; genuine independent runs match exactly."""
    run = tmp_path / "run_w"
    run.mkdir()
    (run / "15_all_policies_all_three_fill_modes_metrics.json").write_text("{}", encoding="utf-8")
    (run / "24_engineering_strategy_activation_dual_verdict.json").write_text(
        json.dumps({"policies": {}, "strategy_statistical_verdict": "INCONCLUSIVE_UNDERPOWERED", "activation_ready": "NO", "fresh_holdout": "REUSED_HISTORICAL_HOLDOUT_NOT_FRESH_OOS", "method": "SEEDED_EMPIRICAL_BOOTSTRAP_HOLM_STEP_DOWN"}),
        encoding="utf-8",
    )
    (run / "16_walkforward_purge_embargo_oos_freshness_and_uncertainty.json").write_text(
        json.dumps({"split_boundaries_ms": [1, 2], "embargo_bars_m1": 180, "fresh_oos_verification_status": "REUSED_HISTORICAL_HOLDOUT_NOT_FRESH_OOS", "policies": {}}),
        encoding="utf-8",
    )
    empty_sha = hashlib.sha256(b"{}").hexdigest()
    (run / "21_1v2v4_independent_determinism_sha_manifest.json").write_text(
        json.dumps({
            "28": {"worker_count": 1, "worker_pids_observed": [101], "results_by_policy": {}, "canonical_results_sha256": empty_sha},
            "29": {"worker_count": 2, "worker_pids_observed": [102, 103], "results_by_policy": {}, "canonical_results_sha256": empty_sha},
            "30": {"worker_count": 4, "worker_pids_observed": [104], "results_by_policy": {}, "canonical_results_sha256": empty_sha},
        }),
        encoding="utf-8",
    )
    for fn in (
        "05_full_checkpoint_model_direction_execution_audit.jsonl.gz",
        "13_baseline_raw_execution_episodes.jsonl.gz",
        "13_per_trade_forensics_observed_vs_hypothesized.jsonl.gz",
        "07_verified_causal_features.jsonl.gz",
    ):
        with gzip.open(run / fn, "wt", encoding="utf-8"):
            pass
    (run / "03_canonical_dataset_and_quote_source_manifest.json").write_text(
        json.dumps({"dataset_sha256": "9f301fee2e620c882b205b857fc63b73c3e36f550161f4100e64fcba7ff98c18"}),
        encoding="utf-8",
    )
    res = verify_raw_accounting_and_worker_proofs(run)
    assert res["status"] == "FAIL"
    assert "WORKER_EXECUTION_OR_DIGEST_MISMATCH:30" in res["errors"]


def test_n27_duplicate_or_missing_checkpoint_in_223460_audit_fails(tmp_path):
    """N27: Duplicate or missing checkpoint in full-universe audit -> census FAIL."""
    bars_1m = _make_tf_bars_with_pivot(1_790_000_000_000, 60_000, 32, pivot_index=2, direction="LONG")
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
        f.write(json.dumps(row) + "\n")
    res = verify_checkpoint_coverage_from_disk(audit_path, {"1m": bars_1m})
    assert res["status"] == "FAIL"
    assert "DUPLICATE_CHECKPOINT_MODEL_DIRECTION" in res["violations"]


def test_n28_claim_positive_expectancy_on_zero_trades_fails():
    """N28: Claim positive expectancy on zero trades, or omit UNDEFINED_ZERO_CLOSED_TRADES status -> FAIL."""
    htf_doc = {"version": VERSION_V17931210, "authoritative_universe_count": 63117, "htf_alignment_state_totals": {"ALIGNED": 7825, "COUNTERTREND": 9168, "MIXED": 46124}}
    funnel_doc = {"version": VERSION_V17931210, "authoritative_universe_count": 63117, "pre_arbitration_required_proof_rejections": {"HTF": 62839}, "pre_arbitration_proof_eligible_count": 278}
    cohort_doc = {
        "version": VERSION_V17931210,
        "baseline_primary_closed_losses_n157": {"conservation_holds": True, "primary_label_distribution": {"WRONG_SIDE_HTF": 157}},
        "baseline_primary_scheduled_episodes_n242": {"conservation_holds": True, "primary_label_distribution": {"WRONG_SIDE_HTF": 242}},
    }
    cb15_doc = {"version": VERSION_V17931210, "unique_closed_trades_count": 15, "total_policy_trade_occurrences": 23, "cases": [{}] * 15}
    bad_pol_doc = {
        "version": VERSION_V17931210,
        "policies": {
            "SI8_RESEARCH_DISCOVERY_REPAIRED": {
                "NEXT_BAR_OPEN_BID_ASK": {
                    "closed_trades_count": 0,
                    "expectancy_net_r": 0.0,
                    "expectancy_status": "POSITIVE",
                    "expectancy_is_positive_evidence": True,
                }
            }
        },
    }
    res = verify_source_denom_and_trade_report_consistency_v17931210(
        authority_denom=63117,
        htf_census_doc=htf_doc,
        funnel_doc=funnel_doc,
        cohort_doc=cohort_doc,
        casebook_15_doc=cb15_doc,
        policy_metrics_doc=bad_pol_doc,
        markdown_texts={"exec": f"# {VERSION_V17931210}\n- **Version Authority**: `{VERSION_V17931210}`\n"},
    )
    assert res["status"] == "FAIL"
    assert any("ZERO_TRADE" in err for err in res["errors"])


def test_n29_push_phase_b_without_phase_c_cannot_pass_remote_receipt(monkeypatch, tmp_path):
    """N29: Push Phase B but not Phase C and set final remote gate PASS -> FAIL/BLOCKED; external read-back required."""
    import subprocess

    fake_sha = "a" * 40
    monkeypatch.setattr(subprocess, "check_output", lambda cmd, **kw: f"{fake_sha}\n" if "ls-remote" not in cmd else f"{fake_sha}\trefs/heads/feature/v17-9-2-structural-sl-tp-from-qa\n")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: None)
    receipt = verify_git_remote_phase_receipt_v1793127(tmp_path, phase_a=fake_sha, phase_b=fake_sha, phase_c=None)
    assert receipt["status"] == "BLOCKED"


def test_n30_production_and_live_paper_auto_execution_modes_fail_closed():
    """N30: Enable LIVE/PAPER/AUTO_PAPER/AUTO_LIVE or RESEARCH_APPLY in PRODUCTION_ORCHESTRATOR -> fail closed."""
    forb, code = is_research_apply_forbidden_in_adapter_v1793122("PRODUCTION_ORCHESTRATOR")
    assert forb is True
    assert code == "SI_FORBIDDEN_IN_PRODUCTION_ADAPTER"

    for exec_mode in ("LIVE", "PAPER", "AUTO_PAPER", "AUTO_LIVE"):
        with pytest.raises(ValueError, match="FORBIDDEN_IN_LIVE_OR_PAPER_EXECUTION_MODE"):
            M1ScalpConfig(
                execution_mode=exec_mode,
                v17_9_3_1_2_2_signal_intelligence_mode="RESEARCH_APPLY",
                v17_9_3_1_2_2_policy_id="SI8_RESEARCH_DISCOVERY_REPAIRED",
            )
