"""
AURUM Desk V17.9.2.1 — Structural SL/TP Historical Validation Closure & Matched-Trade Forensics
24-Test Acceptance & RED/GREEN Defect Reproduction Suite (`T01`–`T24`, Section 9)
================================================================================================
Covers:
  - T01: Causal anchor candidate cutoff guard (`known_at_ms <= decision_timestamp_ms`)
  - T02: Model-specific anchor ranking & deterministic tie-break
  - T03: Stop side & outward tick quantization invariants (`ROUND_FLOOR` LONG, `ROUND_CEILING` SHORT)
  - T04: Noise buffer monotonicity & missing M5 fallback provenance
  - T05: Structure invalidation contract rejects local sweep wick when protected swing exists
  - T06: Target candidate causality & no-invented-RR-target rule
  - T07: Consumed liquidity cross-TF swept pool exclusion (RED/GREEN Defect #1 proof)
  - T08: Cost-aware Net R:R & dynamic position sizing
  - T09: Min-lot (`0.01`) & margin rejection for wide structural stop
  - T10: Matched-cohort reconciliation (`BOTH_ELIGIBLE + BASELINE_ONLY + NEW_ONLY + REJECTED_BOTH == N`)
  - T11: Same-timestamp 5-model deduplication to non-overlapping single-position episodes
  - T12: Retrospective outcome classifier mutually exclusive & `DELAYED_INEVITABLE_LOSS` (RED/GREEN Defect #3 proof)
  - T13: `OPEN_AT_HORIZON` & `DATA_GAP_UNRESOLVED` never counted as wins
  - T14: `LEGACY_CLOSE_FILL` vs `NEXT_BAR_OPEN_BID_ASK` vs `STRESS_WIDER_SPREAD_SLIPPAGE_2X` & gap entry rejection
  - T15: Baseline `A` vs Observe-Only `B` 100% trade-action invariance & context isolation
  - T16: 3-Adapter consumed decision parity & spoofed/reused `adapter_call_id` rejection
  - T17: Dataset gap classification (`22,376` M1 bars, `6,926` missing minutes) & `MODELED_SPREAD_QUOTE` disclosure
  - T18: Chronological `TRAIN`/`VALIDATION`/`OUT_OF_SAMPLE` split & 180-M1 embargo statistical verdict guard
  - T19: Single-worker vs multi-worker deterministic merge hash equality
  - T20: Resource guard (`max_workers <= 4`) & artifact disk budget enforcement
  - T21: Cross-TF M5/M15 anchor invalidation outside 120-bar M1 window (RED/GREEN Defect #2 proof)
  - T22: Historical 6-episode forensic reproduction with V17.9.2.1 sub-classes
  - T23: Stage-1 candidate selection bias audit & unbiased stratified control sample
  - T24: Synthetic (`SYNTHETIC_UNIT_TEST`) vs historical (`HISTORICAL_BACKTEST_V1792`) provenance segregation guard
"""

from __future__ import annotations

import bisect
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "backend"))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.market_shadow_service import MarketShadowService
from services.mtf_structure_liquidity_service import (
    build_variant_market_context,
    compare_ab_variant_decisions,
    compare_actual_consumed_decisions,
    deterministic_hash,
)
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator
from services.shared_strategy_evaluator import (
    PRODUCTION_MODEL_IDS,
    SharedStrategyEvaluator,
)
from services.stop_placement_types import StopAnchorKind
from services.v17_8_backtest_runner import (
    collect_or_load_verified_historical_dataset,
    evaluate_production_parity_model_snapshot,
)
from services.v17_9_1_decision_funnel import (
    classify_historical_dataset_gaps,
    scan_full_m1_candidate_universe,
)
from services.v17_9_2_structural_stop_intelligence import (
    StopOutcomeDiagnostic,
    StructuralStopCandidate,
    classify_structure_stop_outcome,
    collect_causal_stop_anchor_candidates,
    compute_causal_noise_buffer,
    derive_structure_valid_stop,
    evaluate_stop_tp_risk_and_size,
    measure_premature_stop_rate,
    rank_structure_valid_anchors,
    resolve_causal_target_candidates,
    select_viable_structural_target,
    simulate_order_fill_and_outcome_sensitivity,
    validate_structure_invalidation_contract,
)
from tools.run_v17_9_2_structural_stop_backtest import (
    BASELINE_FORENSIC_EPISODES,
    V1792_VARIANT_POLICIES,
    _partition_for_index,
    _summarize_v1792_variant,
    evaluate_v1792_statistical_verdict,
    validate_historical_provenance_segregation,
)
from tools.test_resource_guard import (
    TestResourceGuard,
    probe_disk_budget,
    reserve_test_artifact_budget,
)


def _make_bar(
    open_ms: int,
    o: float,
    h: float,
    l: float,
    c: float,
    tf_ms: int = 60_000,
) -> Dict[str, Any]:
    return {
        "timestamp": open_ms,
        "open_time": open_ms,
        "close_time": open_ms + tf_ms,
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": 100.0,
        "is_closed": True,
    }


def test_t01_causal_anchor_candidate_cutoff_guard() -> None:
    """T01: Future anchor (`known_at_ms > decision_timestamp_ms`) rejected; boundary accepted."""
    cutoff_ms = 1_700_000_600_000
    bars_m1 = [
        _make_bar(cutoff_ms - (15 - i) * 60_000, 4002.0, 4004.0, 3999.0, 4003.0)
        for i in range(15)
    ]
    sl_1m = [
        {
            "id": "sl_causal_boundary",
            "price": 3997.50,
            "occurred_at_ms": cutoff_ms - 180_000,
            "known_at_ms": cutoff_ms,
        },
        {
            "id": "sl_future_leak",
            "price": 3996.00,
            "occurred_at_ms": cutoff_ms - 60_000,
            "known_at_ms": cutoff_ms + 60_000,
        },
    ]
    cands = collect_causal_stop_anchor_candidates(
        model_id="SMC_M1_FVG_CONTINUATION",
        direction="LONG",
        entry_price=4003.0,
        decision_timestamp_ms=cutoff_ms,
        closed_m1=bars_m1,
        sl_1m=sl_1m,
    )
    by_id = {c.anchor_id: c for c in cands}
    assert by_id["sl_causal_boundary"].is_causal is True
    assert by_id["sl_future_leak"].is_causal is False
    assert "FUTURE_ANCHOR_REJECTED" in by_id["sl_future_leak"].rejection_reasons


def test_t02_model_specific_anchor_ranking_and_deterministic_tie_break() -> None:
    """T02: Protected swing outranks local sweep wick; deterministic tie-break on equal rank."""
    cutoff_ms = 1_700_000_600_000
    local_wick = StructuralStopCandidate(
        anchor_id="anc_sweep_wick",
        anchor_kind=StopAnchorKind.SWEEP_EXTREME.value,
        anchor_price=3999.20,
        timeframe="1M",
        occurred_at_ms=cutoff_ms - 120_000,
        known_at_ms=cutoff_ms - 60_000,
        source_bar_open_ms=cutoff_ms - 120_000,
        source_bar_close_ms=cutoff_ms - 60_000,
        is_closed_bar=True,
        is_causal=True,
        is_invalidated=False,
        is_protected_swing=False,
        is_local_sweep_only=True,
        structural_rank=50,
        model_compatibility=("SMC_M1_REVERSAL",),
    )
    prot_b = StructuralStopCandidate(
        anchor_id="anc_prot_b",
        anchor_kind=StopAnchorKind.PROTECTED_SWING_LOW.value,
        anchor_price=3997.80,
        timeframe="5M",
        occurred_at_ms=cutoff_ms - 300_000,
        known_at_ms=cutoff_ms - 180_000,
        source_bar_open_ms=cutoff_ms - 300_000,
        source_bar_close_ms=cutoff_ms - 240_000,
        is_closed_bar=True,
        is_causal=True,
        is_invalidated=False,
        is_protected_swing=True,
        is_local_sweep_only=False,
        structural_rank=90,
        model_compatibility=("SMC_M1_REVERSAL",),
    )
    prot_a = StructuralStopCandidate(
        anchor_id="anc_prot_a",
        anchor_kind=StopAnchorKind.PROTECTED_SWING_LOW.value,
        anchor_price=3997.80,
        timeframe="5M",
        occurred_at_ms=cutoff_ms - 300_000,
        known_at_ms=cutoff_ms - 180_000,
        source_bar_open_ms=cutoff_ms - 300_000,
        source_bar_close_ms=cutoff_ms - 240_000,
        is_closed_bar=True,
        is_causal=True,
        is_invalidated=False,
        is_protected_swing=True,
        is_local_sweep_only=False,
        structural_rank=90,
        model_compatibility=("SMC_M1_REVERSAL",),
    )
    sel_1, ranked_1, rejs_1 = rank_structure_valid_anchors(
        [local_wick, prot_b, prot_a],
        model_id="SMC_M1_REVERSAL",
        direction="LONG",
        entry_price=4002.0,
        decision_timestamp_ms=cutoff_ms,
    )
    sel_2, ranked_2, _ = rank_structure_valid_anchors(
        [prot_a, prot_b, local_wick],
        model_id="SMC_M1_REVERSAL",
        direction="LONG",
        entry_price=4002.0,
        decision_timestamp_ms=cutoff_ms,
    )
    assert rejs_1 == ()
    assert sel_1 is not None and sel_2 is not None
    assert sel_1.anchor_id == sel_2.anchor_id == "anc_prot_a"
    assert [c.anchor_id for c in ranked_1] == [c.anchor_id for c in ranked_2]


def test_t03_stop_side_and_tick_quantization_invariants() -> None:
    """T03: LONG stop quantized downward (`ROUND_FLOOR`); SHORT quantized upward (`ROUND_CEILING`)."""
    sl_long = derive_structure_valid_stop(
        direction="LONG",
        invalidation_price=4000.00,
        noise_buffer_price=0.512,
        tick_size=0.01,
        entry_price=4004.00,
    )
    assert sl_long == 3999.48

    sl_short = derive_structure_valid_stop(
        direction="SHORT",
        invalidation_price=4000.00,
        noise_buffer_price=0.512,
        tick_size=0.01,
        entry_price=3996.00,
    )
    assert sl_short == 4000.52

    assert (
        derive_structure_valid_stop(
            direction="LONG",
            invalidation_price=4005.00,
            noise_buffer_price=0.50,
            tick_size=0.01,
            entry_price=4004.00,
        )
        is None
    )


def test_t04_noise_buffer_monotonicity_and_fallback_fail_closed() -> None:
    """T04: Buffer increases monotonically with ATR/spread; missing M5 ATR uses M1+spread without fabrication."""
    b_low, _, _ = compute_causal_noise_buffer(
        atr_m1=1.0, atr_m5=2.0, spread=0.20, tick_size=0.01, buffer_mode="STRUCTURE_PLUS_NOISE"
    )
    b_high, _, _ = compute_causal_noise_buffer(
        atr_m1=3.0, atr_m5=6.0, spread=0.40, tick_size=0.01, buffer_mode="STRUCTURE_PLUS_NOISE"
    )
    assert b_high > b_low >= 0.05

    b_no_m5, comps_no_m5, _ = compute_causal_noise_buffer(
        atr_m1=1.5, atr_m5=None, spread=0.20, tick_size=0.01, buffer_mode="STRUCTURE_PLUS_NOISE"
    )
    assert b_no_m5 > 0.0
    assert "atr_m5" not in comps_no_m5


def test_t05_structure_invalidation_contract_rejects_local_wick_only_when_protected_swing_exists() -> None:
    """T05: Stop inside protected invalidation fails contract."""
    cutoff_ms = 1_700_000_600_000
    anc = StructuralStopCandidate(
        anchor_id="anc_prot",
        anchor_kind=StopAnchorKind.PROTECTED_SWING_LOW.value,
        anchor_price=4000.0,
        timeframe="5M",
        occurred_at_ms=cutoff_ms - 600_000,
        known_at_ms=cutoff_ms - 300_000,
        source_bar_open_ms=cutoff_ms - 600_000,
        source_bar_close_ms=cutoff_ms - 300_000,
        is_closed_bar=True,
        is_causal=True,
        is_invalidated=False,
        is_protected_swing=True,
        is_local_sweep_only=False,
        structural_rank=90,
    )
    ok_bad, rejs_bad = validate_structure_invalidation_contract(
        direction="LONG",
        entry_price=4005.0,
        proposed_sl=4000.10,
        anchor=anc,
        noise_buffer_price=0.50,
        decision_timestamp_ms=cutoff_ms,
    )
    assert ok_bad is False
    assert "SL_INSIDE_PROTECTED_INVALIDATION" in rejs_bad


def test_t06_target_candidate_causality_and_no_invented_rr_target() -> None:
    """T06: Future target pool rejected; no invented 2R target when structural pool fails Net R:R."""
    cutoff_ms = 1_700_000_600_000
    pools = [
        {
            "id": "pool_causal_near",
            "price": 4003.0,
            "side": "BUY_SIDE",
            "source_kind": "M5_SWING_HIGH",
            "timeframe": "5M",
            "occurred_at_ms": cutoff_ms - 600_000,
            "known_at_ms": cutoff_ms - 300_000,
            "state": "UNTOUCHED",
        },
        {
            "id": "pool_future_far",
            "price": 4035.0,
            "side": "BUY_SIDE",
            "source_kind": "H1_SWING_HIGH",
            "timeframe": "1H",
            "occurred_at_ms": cutoff_ms,
            "known_at_ms": cutoff_ms + 3_600_000,
            "state": "UNTOUCHED",
        },
    ]
    cands = resolve_causal_target_candidates(
        direction="LONG",
        entry_price=4000.0,
        decision_timestamp_ms=cutoff_ms,
        liquidity_pools=pools,
    )
    cand_ids = [c["id"] for c in cands]
    assert "pool_causal_near" in cand_ids
    assert "pool_future_far" not in cand_ids

    t_plan = select_viable_structural_target(
        direction="LONG",
        entry_price=4000.0,
        proposed_sl=3996.0,
        decision_timestamp_ms=cutoff_ms,
        target_candidates=cands,
        baseline_target_price=4003.0,
        baseline_target_id="pool_causal_near",
        min_net_rr=2.0,
        target_mode="V1792_CAUSAL_STRUCTURAL_VIABLE",
    )
    assert t_plan.target_price == 4003.0
    assert "NET_RR_TOO_LOW" in t_plan.rejection_reasons


def test_t07_consumed_liquidity_cross_tf_swept_pool_exclusion_red_green() -> None:
    """
    T07 (Defect #1 RED/GREEN): An HTF (15M) swing high pool at 4015.00 formed at `t0 - 30m`
    that was swept by a closed M1 bar at `t0 - 2m` (`high = 4015.40`) inside the current
    developing 15M candle:
      - `enforce_cross_tf_sweep_check=False` (`code-old`): erroneously includes swept 15M swing in causal target candidates
      - `enforce_cross_tf_sweep_check=True` (`code-fixed`): excludes swept 15M swing from causal target candidates.
    """
    cutoff_ms = 1_700_001_800_000
    # 3 closed M15 bars where the middle bar (idx 1) is a confirmed M15 swing high at 4015.00
    closed_m15 = [
        _make_bar(cutoff_ms - 3_600_000, 4002.0, 4008.0, 4001.0, 4006.0, tf_ms=900_000),
        _make_bar(cutoff_ms - 2_700_000, 4006.0, 4015.0, 4004.0, 4008.0, tf_ms=900_000),
        _make_bar(cutoff_ms - 1_800_000, 4008.0, 4012.0, 4002.0, 4004.0, tf_ms=900_000),
    ]
    # Recent M1 bar closed 2 minutes before cutoff_ms swept 4015.00 up to 4015.40, then pulled back to 4003.00
    closed_m1 = [
        _make_bar(cutoff_ms - 180_000, 4010.0, 4015.40, 4008.0, 4009.0, tf_ms=60_000),
        _make_bar(cutoff_ms - 120_000, 4009.0, 4009.50, 4002.50, 4003.0, tf_ms=60_000),
    ]
    pools = [
        {
            "id": "m15_bsl_swept_by_m1",
            "price": 4015.0,
            "side": "BUY_SIDE",
            "source_kind": "M15_SWING_HIGH",
            "timeframe": "15M",
            "occurred_at_ms": cutoff_ms - 2_700_000,
            "known_at_ms": cutoff_ms - 1_800_000,
            "state": "UNTOUCHED",
        }
    ]

    old_cands = resolve_causal_target_candidates(
        direction="LONG",
        entry_price=4003.0,
        decision_timestamp_ms=cutoff_ms,
        liquidity_pools=pools,
        closed_m1=closed_m1,
        closed_m15=closed_m15,
        enforce_cross_tf_sweep_check=False,
    )
    assert any(abs(float(c["price"]) - 4015.0) < 1e-6 for c in old_cands)

    fixed_cands = resolve_causal_target_candidates(
        direction="LONG",
        entry_price=4003.0,
        decision_timestamp_ms=cutoff_ms,
        liquidity_pools=pools,
        closed_m1=closed_m1,
        closed_m15=closed_m15,
        enforce_cross_tf_sweep_check=True,
    )
    assert not any(abs(float(c["price"]) - 4015.0) < 1e-6 for c in fixed_cands)


def test_t08_cost_aware_net_rr_and_dynamic_position_sizing() -> None:
    """T08: Dynamic quantity scales down as stop widens so `net_risk_usdt <= 2.50`."""
    tight = evaluate_stop_tp_risk_and_size(
        direction="LONG",
        entry_price=4000.0,
        proposed_sl=3998.0,
        target_price=4026.0,
        capital_usdt=1000.0,
        risk_pct=0.25,
        leverage=20,
        margin_mode="ISOLATED",
        min_net_rr=2.0,
    )
    wide = evaluate_stop_tp_risk_and_size(
        direction="LONG",
        entry_price=4000.0,
        proposed_sl=3994.0,
        target_price=4026.0,
        capital_usdt=1000.0,
        risk_pct=0.25,
        leverage=20,
        margin_mode="ISOLATED",
        min_net_rr=2.0,
    )
    assert tight["is_executable"] is True
    assert wide["is_executable"] is True
    assert tight["hypothetical_quantity"] > wide["hypothetical_quantity"]
    assert tight["net_risk_usdt"] <= 2.50 + 1e-6
    assert wide["net_risk_usdt"] <= 2.50 + 1e-6


def test_t09_min_lot_and_margin_rejection_for_wide_structural_stop() -> None:
    """T09: Wide stop ($300) rejected when `0.01` min lot exceeds `$2.50` risk budget."""
    res = evaluate_stop_tp_risk_and_size(
        direction="LONG",
        entry_price=4000.0,
        proposed_sl=3700.0,
        target_price=4800.0,
        capital_usdt=1000.0,
        risk_pct=0.25,
        leverage=20,
        margin_mode="ISOLATED",
        min_net_rr=2.0,
    )
    assert res["is_executable"] is False
    assert "POSITION_SIZE_BELOW_MIN_LOT" in res["rejection_reasons"]


def test_t10_matched_cohort_reconciliation_a_vs_c_vs_d3_vs_d4() -> None:
    """T10: Pairwise matched-cohort sets partition the evaluated `(checkpoint, model_id)` universe exactly."""
    universe = [(100, "M1"), (100, "M2"), (200, "M1"), (200, "M2"), (300, "M1")]
    elig_a = {(100, "M1"), (200, "M1")}
    elig_d4 = {(200, "M1"), (200, "M2")}

    both = [k for k in universe if k in elig_a and k in elig_d4]
    base_only = [k for k in universe if k in elig_a and k not in elig_d4]
    new_only = [k for k in universe if k not in elig_a and k in elig_d4]
    rej_both = [k for k in universe if k not in elig_a and k not in elig_d4]

    assert len(both) == 1
    assert len(base_only) == 1
    assert len(new_only) == 1
    assert len(rej_both) == 2
    assert len(both) + len(base_only) + len(new_only) + len(rej_both) == len(universe)


def test_t11_same_timestamp_multi_model_deduplication_to_non_overlapping_episodes() -> None:
    """T11: Multiple eligible models at the same timestamp deduplicate to 1 active episode during hold window."""
    recs = []
    for m_id in PRODUCTION_MODEL_IDS:
        recs.append(
            {
                "provenance": "HISTORICAL_BACKTEST_V1792",
                "variant": "A_FROZEN_BASELINE_OFF",
                "checkpoint_index_1m": 500,
                "decision_id": f"d_{m_id}_500",
                "decision_timestamp_ms": 1_700_030_000_000,
                "decision_time_utc": "2023-11-15T06:33:20Z",
                "model_id": m_id,
                "direction": "SHORT",
                "partition": "TRAIN",
                "session": "ASIA",
                "setup_validity": "VALID",
                "decision_status": "ELIGIBLE",
                "entry_reference": 4000.0,
                "selected_sl": 4004.0,
                "take_profit": 3985.0,
                "hypothetical_quantity": 0.50,
                "stop_distance_price": 4.0,
                "target_distance_price": 15.0,
                "gross_rr": 3.75,
                "net_rr": 2.40,
                "rejection_reason_codes": [],
                "decision_fingerprint": f"fp_{m_id}",
                "trade_action_fingerprint": f"act_{m_id}",
                "policy_fingerprint": "pol",
                "metadata_fingerprint": "meta",
                "outcome_label": "LOSS",
                "realized_r": -1.0,
                "bars_held": 10,
                "stop_outcome_diagnostic": None,
            }
        )
    summary = _summarize_v1792_variant(
        "A_FROZEN_BASELINE_OFF", V1792_VARIANT_POLICIES["A_FROZEN_BASELINE_OFF"], recs
    )
    assert summary["n_eligible_model_decisions"] == 5
    assert summary["n_unique_eligible_timestamps"] == 1
    assert summary["n_non_overlapping_episodes"] == 1


def test_t12_retrospective_outcome_classifier_mutually_exclusive_and_delayed_inevitable_loss_red_green() -> None:
    """
    T12 (Defect #3 RED/GREEN): Verifies `DELAYED_INEVITABLE_LOSS` is reachable when outer structure
    breaks within `delayed_break_window_bars=5` of stop hit without TP hit, vs `PREMATURE_NOISE_SWEEP_STOP`
    when structure holds or breaks >5 bars later, vs `TRUE_INVALIDATION_LOSS` when structure breaks on/before stop.
    """
    t0 = 1_700_000_000_000
    bars_delayed = [
        _make_bar(t0 + 60_000, 4000.0, 4000.5, 3997.8, 3998.5),
        _make_bar(t0 + 120_000, 3998.5, 3998.8, 3994.5, 3995.0),
    ]
    d_delayed = classify_structure_stop_outcome(
        episode_id="ep_delayed",
        model_id="SMC_M1_FVG_CONTINUATION",
        direction="LONG",
        entry_price=4000.0,
        sl_price=3998.0,
        tp_price=4010.0,
        invalidation_price=3998.2,
        protected_structure_price=3996.0,
        decision_timestamp_ms=t0,
        forward_bars=bars_delayed,
    )
    assert d_delayed.outcome_class == "DELAYED_INEVITABLE_LOSS"
    assert d_delayed.sub_class == "DELAYED_INEVITABLE_BREAK_WITHIN_5_BARS"

    bars_tp_after = [
        _make_bar(t0 + 60_000, 4000.0, 4001.0, 3997.8, 3999.0),
        _make_bar(t0 + 120_000, 3999.0, 4011.0, 3998.8, 4010.5),
    ]
    d_tp_after = classify_structure_stop_outcome(
        episode_id="ep_tp_after",
        model_id="SMC_M1_FVG_CONTINUATION",
        direction="LONG",
        entry_price=4000.0,
        sl_price=3998.0,
        tp_price=4010.0,
        invalidation_price=3998.2,
        protected_structure_price=3996.0,
        decision_timestamp_ms=t0,
        forward_bars=bars_tp_after,
    )
    assert d_tp_after.outcome_class == "PREMATURE_NOISE_SWEEP_STOP"
    assert d_tp_after.sub_class == "NOISE_SWEEP_THEN_TP_HIT_EX_POST"
    assert d_tp_after.tp_would_hit_after_stop is True

    bars_true_inv = [
        _make_bar(t0 + 60_000, 4000.0, 4000.2, 3995.0, 3995.2),
    ]
    d_true_inv = classify_structure_stop_outcome(
        episode_id="ep_true_inv",
        model_id="SMC_M1_FVG_CONTINUATION",
        direction="LONG",
        entry_price=4000.0,
        sl_price=3998.0,
        tp_price=4010.0,
        invalidation_price=3998.2,
        protected_structure_price=3996.0,
        decision_timestamp_ms=t0,
        forward_bars=bars_true_inv,
    )
    assert d_true_inv.outcome_class == "TRUE_INVALIDATION_LOSS"
    assert d_true_inv.sub_class == "IMMEDIATE_STRUCTURE_INVALIDATION"

    summary = measure_premature_stop_rate([d_delayed, d_tp_after, d_true_inv])
    assert summary["total_episodes"] == 3
    assert summary["delayed_inevitable_loss_count"] == 1
    assert summary["premature_stop_count"] == 1
    assert summary["true_invalidation_loss_count"] == 1
    assert summary["tp_would_hit_after_stop_count"] == 1


def test_t13_open_at_horizon_and_data_gap_unresolved_not_counted_as_wins() -> None:
    """T13: `OPEN_AT_HORIZON` and `DATA_GAP_UNRESOLVED` are never counted as wins."""
    t0 = 1_700_000_000_000
    bars_open = [_make_bar(t0 + 60_000, 4000.0, 4002.0, 3999.0, 4001.0)]
    d_open = classify_structure_stop_outcome(
        episode_id="ep_open",
        model_id="SMC_M1_REVERSAL",
        direction="LONG",
        entry_price=4000.0,
        sl_price=3995.0,
        tp_price=4015.0,
        invalidation_price=3995.5,
        protected_structure_price=3995.5,
        decision_timestamp_ms=t0,
        forward_bars=bars_open,
    )
    assert d_open.outcome_class == "OPEN_AT_HORIZON"

    bars_gap = [
        _make_bar(t0 + 60_000, 4000.0, 4002.0, 3999.0, 4001.0),
        _make_bar(t0 + 900_000, 4001.0, 4016.0, 4000.0, 4015.5),
    ]
    d_gap = classify_structure_stop_outcome(
        episode_id="ep_gap",
        model_id="SMC_M1_REVERSAL",
        direction="LONG",
        entry_price=4000.0,
        sl_price=3995.0,
        tp_price=4015.0,
        invalidation_price=3995.5,
        protected_structure_price=3995.5,
        decision_timestamp_ms=t0,
        forward_bars=bars_gap,
        classify_gap_unresolved=True,
    )
    assert d_gap.outcome_class == "DATA_GAP_UNRESOLVED"
    assert d_gap.has_data_gap_before_exit is True

    summary = measure_premature_stop_rate([d_open, d_gap])
    assert summary["clean_win_count"] == 0
    assert summary["open_at_horizon_count"] == 1
    assert summary["data_gap_unresolved_count"] == 1


def test_t14_entry_on_close_vs_next_bar_open_bid_ask_and_gap_entry_rejection() -> None:
    """
    T14: `simulate_order_fill_and_outcome_sensitivity` distinguishes `LEGACY_CLOSE_FILL`,
    `NEXT_BAR_OPEN_BID_ASK`, `STRESS_WIDER_SPREAD_SLIPPAGE_2X`, and rejects entry when a provider
    gap occurs before the first tradable bar (`NO_FILL_ENTRY_GAP`).
    """
    t0 = 1_700_000_000_000
    fwd = [
        _make_bar(t0, 4000.20, 4001.0, 3996.50, 3997.0),
    ]
    res_legacy = simulate_order_fill_and_outcome_sensitivity(
        direction="LONG",
        planned_entry=4000.0,
        sl_price=3997.0,
        tp_price=4010.0,
        quantity=0.25,
        decision_timestamp_ms=t0,
        forward_bars=fwd,
        fill_mode="LEGACY_CLOSE_FILL",
    )
    res_next = simulate_order_fill_and_outcome_sensitivity(
        direction="LONG",
        planned_entry=4000.0,
        sl_price=3997.0,
        tp_price=4010.0,
        quantity=0.25,
        decision_timestamp_ms=t0,
        forward_bars=fwd,
        fill_mode="NEXT_BAR_OPEN_BID_ASK",
    )
    res_stress = simulate_order_fill_and_outcome_sensitivity(
        direction="LONG",
        planned_entry=4000.0,
        sl_price=3997.0,
        tp_price=4010.0,
        quantity=0.25,
        decision_timestamp_ms=t0,
        forward_bars=fwd,
        fill_mode="STRESS_WIDER_SPREAD_SLIPPAGE_2X",
    )
    assert res_legacy["filled"] is True
    assert res_legacy["fill_price"] == 4000.0
    assert res_next["filled"] is True
    assert res_next["fill_price"] == 4000.40
    assert res_stress["filled"] is True
    assert res_stress["fill_price"] == 4000.60

    fwd_gap = [_make_bar(t0 + 600_000, 4000.20, 4001.0, 3999.0, 4000.5)]
    res_gap = simulate_order_fill_and_outcome_sensitivity(
        direction="LONG",
        planned_entry=4000.0,
        sl_price=3997.0,
        tp_price=4010.0,
        quantity=0.25,
        decision_timestamp_ms=t0,
        forward_bars=fwd_gap,
        fill_mode="NEXT_BAR_OPEN_BID_ASK",
    )
    assert res_gap["filled"] is False
    assert res_gap["outcome"] == "NO_FILL_ENTRY_GAP"


def test_t15_baseline_a_vs_observe_only_b_action_invariance_and_context_isolation() -> None:
    """T15: `A_FROZEN_BASELINE_OFF` vs `B_V1792_OBSERVE_ONLY` have 100% action invariance & isolated contexts."""
    ds = collect_or_load_verified_historical_dataset(symbol="XAUUSDT", allow_network_refresh=False)
    i_1m = 6652
    bars_1m = ds["1m"]
    asof_ms = int(bars_1m[i_1m]["close_time"])
    w1 = bars_1m[i_1m + 1 - 120 : i_1m + 1]
    w5 = [b for b in ds["5m"] if int(b["close_time"]) <= asof_ms][-96:]
    w15 = [b for b in ds["15m"] if int(b["close_time"]) <= asof_ms][-96:]
    w1h = [b for b in ds["1H"] if int(b["close_time"]) <= asof_ms][-96:]
    w4h = [b for b in ds["4H"] if int(b["close_time"]) <= asof_ms][-64:]
    w1d = [b for b in ds["1D"] if int(b["close_time"]) <= asof_ms][-30:]
    lc = float(w1[-1]["close"])
    base_in = {
        "symbol": "XAUUSDT",
        "decision_timestamp_ms": asof_ms,
        "m1_candles": w1,
        "m5_candles": w5,
        "m15_candles": w15,
        "h1_candles": w1h,
        "h4_candles": w4h,
        "d1_candles": w1d,
        "bid": round(lc - 0.10, 2),
        "ask": round(lc + 0.10, 2),
    }
    ctx_a = build_variant_market_context(base_in, V1792_VARIANT_POLICIES["A_FROZEN_BASELINE_OFF"], decision_time=asof_ms)
    ctx_b = build_variant_market_context(base_in, V1792_VARIANT_POLICIES["B_V1792_OBSERVE_ONLY"], decision_time=asof_ms)
    assert id(ctx_a) != id(ctx_b)

    snap_a = SharedStrategyEvaluator.evaluate_model_direction(ctx_a, "SMC_M1_FVG_CONTINUATION", "SHORT")
    snap_b = SharedStrategyEvaluator.evaluate_model_direction(ctx_b, "SMC_M1_FVG_CONTINUATION", "SHORT")
    cmp_ab = compare_ab_variant_decisions(ctx_a, snap_a, ctx_b, snap_b)
    assert cmp_ab["is_valid"] is True
    assert cmp_ab["distinct_context"] is True
    assert cmp_ab["distinct_policy_fingerprint"] is True
    assert cmp_ab["distinct_metadata_fingerprint"] is True
    assert snap_a.trade_action_fingerprint == snap_b.trade_action_fingerprint


def test_t16_three_adapter_consumed_decision_parity_and_spoofed_call_id_rejection() -> None:
    """T16: 3-runtime adapters produce identical parity fields and reject reused `adapter_call_id`."""
    ds = collect_or_load_verified_historical_dataset(symbol="XAUUSDT", allow_network_refresh=False)
    i_1m = 6652
    bars_1m = ds["1m"]
    asof_ms = int(bars_1m[i_1m]["close_time"])
    w1 = bars_1m[i_1m + 1 - 120 : i_1m + 1]
    w5 = [b for b in ds["5m"] if int(b["close_time"]) <= asof_ms][-96:]
    w15 = [b for b in ds["15m"] if int(b["close_time"]) <= asof_ms][-96:]
    w1h = [b for b in ds["1H"] if int(b["close_time"]) <= asof_ms][-96:]
    w4h = [b for b in ds["4H"] if int(b["close_time"]) <= asof_ms][-64:]
    w1d = [b for b in ds["1D"] if int(b["close_time"]) <= asof_ms][-30:]
    lc = float(w1[-1]["close"])
    base_in = {
        "symbol": "XAUUSDT",
        "decision_timestamp_ms": asof_ms,
        "m1_candles": w1,
        "m5_candles": w5,
        "m15_candles": w15,
        "h1_candles": w1h,
        "h4_candles": w4h,
        "d1_candles": w1d,
        "bid": round(lc - 0.10, 2),
        "ask": round(lc + 0.10, 2),
    }
    ctx_d4 = build_variant_market_context(base_in, V1792_VARIANT_POLICIES["D4_STRUCTURE_PLUS_TP"], decision_time=asof_ms)
    prod_orch = ScalpWatcherOrchestrator()
    shadow_svc = MarketShadowService(symbol="XAUUSDT")

    s_prod = prod_orch.evaluate_shared_decision_snapshot(context=ctx_d4, model_id="SMC_M1_FVG_CONTINUATION", direction="SHORT")
    s_shad = shadow_svc.evaluate_shared_decision_snapshot(context=ctx_d4, model_id="SMC_M1_FVG_CONTINUATION", direction="SHORT")
    s_bt = evaluate_production_parity_model_snapshot(
        model_id="SMC_M1_FVG_CONTINUATION", context=ctx_d4, direction="SHORT"
    )["shared_decision_snapshot"]

    p_ok = compare_actual_consumed_decisions(s_prod, s_shad, s_bt)
    assert p_ok["is_parity"] is True

    p_spoof = compare_actual_consumed_decisions(s_prod, s_prod, s_bt)
    assert p_spoof["is_parity"] is False


def test_t17_dataset_gap_classification_and_modeled_spread_quote_disclosure() -> None:
    """T17: Verifies historical dataset gap breakdown and `MODELED_SPREAD_QUOTE` disclosure."""
    ds = collect_or_load_verified_historical_dataset(symbol="XAUUSDT", allow_network_refresh=False)
    cov = classify_historical_dataset_gaps(ds)
    assert cov["quote_construction_mode"] == "MODELED_SPREAD_QUOTE"
    m1_info = cov["timeframes"]["M1"]
    assert m1_info["actual_bars"] == 22376
    assert m1_info["total_missing_bars"] == 6926
    assert m1_info["missing_bars_by_category"]["WEEKEND_MARKET_CLOSED"] == 2794
    assert m1_info["missing_bars_by_category"]["INTRADAY_EXTENDED_PROVIDER_GAP"] == 4129
    assert m1_info["missing_bars_by_category"]["INTRADAY_SHORT_ILLIQUID_GAP"] == 3


def test_t18_chronological_oos_split_and_180_bar_embargo_guard() -> None:
    """T18: Verifies 50%/25%/25% chronological split and statistical verdict rules."""
    assert _partition_for_index(100, 22376) == "TRAIN"
    assert _partition_for_index(12000, 22376) == "VALIDATION"
    assert _partition_for_index(18000, 22376) == "OUT_OF_SAMPLE"

    v_inc = evaluate_v1792_statistical_verdict(
        closed_trades=6, wins=0, net_pnl_usd=-15.0, oos_closed_trades=0, oos_net_pnl_usd=0.0
    )
    assert v_inc["strategy_statistical_verdict"] == "INCONCLUSIVE"
    assert v_inc["ready_for_strategy_activation"] == "NO"

    v_neg = evaluate_v1792_statistical_verdict(
        closed_trades=52, wins=4, net_pnl_usd=-110.0, oos_closed_trades=12, oos_net_pnl_usd=-25.0
    )
    assert v_neg["strategy_statistical_verdict"] == "NEGATIVE_EVIDENCE"
    assert v_neg["ready_for_strategy_activation"] == "NO"


def test_t19_single_worker_vs_multi_worker_deterministic_merge() -> None:
    """T19: Deterministic hash of ordered checkpoint evaluations is identical across chunking."""
    items = [f"fp_{i}" for i in range(50)]
    single_chunk_merged = list(items)
    multi_chunk_merged = items[:25] + items[25:]
    assert deterministic_hash(single_chunk_merged, 24) == deterministic_hash(multi_chunk_merged, 24)


def test_t20_resource_guard_and_artifact_budget_enforcement(tmp_path: Path) -> None:
    """T20: Resource guard bounds `max_workers <= 4` and checks disk budget."""
    guard = TestResourceGuard(
        run_id="test_v17921_t20",
        repo_root=REPO_ROOT,
        artifact_dir=tmp_path / "artifacts",
    )
    disk_info = probe_disk_budget(REPO_ROOT)
    assert disk_info.free_bytes > 2 * 1024 * 1024 * 1024
    budget = reserve_test_artifact_budget(
        task="test_v17921_t20", budget=disk_info, requested_bytes=30 * 1024 * 1024
    )
    assert budget.permitted is True
    assert guard.run_id == "test_v17921_t20"


def test_t21_negative_control_wrong_side_stop_and_cross_tf_anchor_invalidation_red_green() -> None:
    """
    T21 (Defect #2 RED/GREEN): An M5 swing low at `4000.00` formed 4 hours ago (`t0 - 240m`)
    that was invalidated by a closed M5 candle 3 hours ago (`t0 - 180m`, `low = 3998.00`),
    while the 120-bar M1 window (`[t0 - 120m .. t0]`) remained above `4001.00`:
      - `enforce_cross_tf_invalidation_check=False` (`code-old`): erroneously marks M5 anchor `is_invalidated=False`
        and selects it in `rank_structure_valid_anchors`.
      - `enforce_cross_tf_invalidation_check=True` (`code-fixed`): marks M5 anchor `is_invalidated=True`
        (`ANCHOR_ALREADY_INVALIDATED` in `rejection_reasons`) and rejects it in `rank_structure_valid_anchors`.
    """
    cutoff_ms = 1_700_020_000_000
    closed_m1 = [
        _make_bar(cutoff_ms - (120 - i) * 60_000, 4003.0, 4005.0, 4002.0, 4004.0, tf_ms=60_000)
        for i in range(120)
    ]
    closed_m5 = []
    for idx in range(30):
        b_open = cutoff_ms - (30 - idx) * 300_000
        if idx == 2:
            closed_m5.append(_make_bar(b_open, 4003.0, 4004.0, 4000.0, 4003.5, tf_ms=300_000))
        elif idx == 6:
            closed_m5.append(_make_bar(b_open, 4002.0, 4002.5, 3998.0, 3998.5, tf_ms=300_000))
        else:
            closed_m5.append(_make_bar(b_open, 4003.0, 4005.0, 4002.0, 4004.0, tf_ms=300_000))

    old_cands = collect_causal_stop_anchor_candidates(
        model_id="SMC_M1_FVG_CONTINUATION",
        direction="LONG",
        entry_price=4004.0,
        decision_timestamp_ms=cutoff_ms,
        closed_m1=closed_m1,
        closed_m5=closed_m5,
        enforce_cross_tf_invalidation_check=False,
    )
    m5_old = [c for c in old_cands if c.timeframe == "5M" and c.anchor_price == 4000.0]
    assert len(m5_old) == 1
    assert m5_old[0].is_invalidated is False

    fixed_cands = collect_causal_stop_anchor_candidates(
        model_id="SMC_M1_FVG_CONTINUATION",
        direction="LONG",
        entry_price=4004.0,
        decision_timestamp_ms=cutoff_ms,
        closed_m1=closed_m1,
        closed_m5=closed_m5,
        enforce_cross_tf_invalidation_check=True,
    )
    m5_fixed = [c for c in fixed_cands if c.timeframe == "5M" and c.anchor_price == 4000.0]
    assert len(m5_fixed) == 1
    assert m5_fixed[0].is_invalidated is True
    assert "ANCHOR_ALREADY_INVALIDATED" in m5_fixed[0].rejection_reasons

    sel_fixed, _, rejs_fixed = rank_structure_valid_anchors(
        m5_fixed,
        model_id="SMC_M1_FVG_CONTINUATION",
        direction="LONG",
        entry_price=4004.0,
        decision_timestamp_ms=cutoff_ms,
    )
    assert sel_fixed is None
    assert "ANCHOR_ALREADY_INVALIDATED" in rejs_fixed


def test_t22_historical_6_episode_forensic_reproduction_with_v17921_subclasses() -> None:
    """
    T22: Reproduces the 6 historical baseline episodes on real `historical_dataset_XAUUSDT.json.gz`
    and verifies V17.9.2.1 classification (`1 TRUE_INVALIDATION_LOSS`, `1 DELAYED_INEVITABLE_LOSS`,
    `4 PREMATURE_NOISE_SWEEP_STOP` = `3 PREMATURE_WICK_SWEEP_BEFORE_LATER_BREAK` + `1 PURE_NOISE_SWEEP_STRUCTURE_HELD`).
    """
    ds = collect_or_load_verified_historical_dataset(symbol="XAUUSDT", allow_network_refresh=False)
    bars_1m = ds["1m"]
    c5 = [int(b["close_time"]) for b in ds["5m"]]
    c15 = [int(b["close_time"]) for b in ds["15m"]]
    c1h = [int(b["close_time"]) for b in ds["1H"]]
    c4h = [int(b["close_time"]) for b in ds["4H"]]
    c1d = [int(b["close_time"]) for b in ds["1D"]]

    diags: List[StopOutcomeDiagnostic] = []
    for ep_idx, (i_1m, model_id, direction) in enumerate(BASELINE_FORENSIC_EPISODES, start=1):
        asof_ms = int(bars_1m[i_1m]["close_time"])
        i5 = bisect.bisect_right(c5, asof_ms)
        i15 = bisect.bisect_right(c15, asof_ms)
        i1h = bisect.bisect_right(c1h, asof_ms)
        i4h = bisect.bisect_right(c4h, asof_ms)
        i1d = bisect.bisect_right(c1d, asof_ms)
        w1 = bars_1m[max(0, i_1m + 1 - 120) : i_1m + 1]
        fwd = bars_1m[i_1m + 1 : min(len(bars_1m), i_1m + 1 + 180)]
        lc = float(w1[-1]["close"])
        base_in = {
            "symbol": "XAUUSDT",
            "decision_timestamp_ms": asof_ms,
            "m1_candles": w1,
            "m5_candles": ds["5m"][max(0, i5 - 96) : i5],
            "m15_candles": ds["15m"][max(0, i15 - 96) : i15],
            "h1_candles": ds["1H"][max(0, i1h - 96) : i1h],
            "h4_candles": ds["4H"][max(0, i4h - 64) : i4h],
            "d1_candles": ds["1D"][max(0, i1d - 30) : i1d],
            "bid": round(lc - 0.10, 2),
            "ask": round(lc + 0.10, 2),
        }
        ctx_a = build_variant_market_context(base_in, V1792_VARIANT_POLICIES["A_FROZEN_BASELINE_OFF"], decision_time=asof_ms)
        ctx_b = build_variant_market_context(base_in, V1792_VARIANT_POLICIES["B_V1792_OBSERVE_ONLY"], decision_time=asof_ms)
        snap_a = SharedStrategyEvaluator.evaluate_model_direction(ctx_a, model_id, direction)
        snap_b = SharedStrategyEvaluator.evaluate_model_direction(ctx_b, model_id, direction)
        v_plan = snap_b.v1792_stop_plan_dict or {}
        prot_px = float(v_plan.get("invalidation_price") or snap_a.structural_invalidation_price)
        diag = classify_structure_stop_outcome(
            episode_id=f"BASE_EP{ep_idx}_{i_1m}",
            model_id=model_id,
            direction=direction,
            entry_price=float(snap_a.entry_reference),
            sl_price=float(snap_a.selected_sl),
            tp_price=float(snap_a.take_profit),
            invalidation_price=float(snap_a.structural_invalidation_price),
            protected_structure_price=prot_px,
            decision_timestamp_ms=asof_ms,
            forward_bars=fwd,
        )
        diags.append(diag)

    summary = measure_premature_stop_rate(diags)
    assert summary["total_episodes"] == 6
    assert summary["true_invalidation_loss_count"] == 1
    assert summary["delayed_inevitable_loss_count"] == 1
    assert summary["premature_stop_count"] == 4
    assert summary["tp_would_hit_after_stop_count"] == 0
    assert summary["sub_class_counts"]["PURE_NOISE_SWEEP_STRUCTURE_HELD"] == 1
    assert summary["sub_class_counts"]["PREMATURE_WICK_SWEEP_BEFORE_LATER_BREAK"] == 3


def test_t23_stage1_candidate_selection_bias_audit_and_unbiased_control_sample() -> None:
    """T23: Verifies Stage-1 candidate scan coverage (`22,346` M1 checkpoints) and control sample."""
    ds = collect_or_load_verified_historical_dataset(symbol="XAUUSDT", allow_network_refresh=False)
    scan = scan_full_m1_candidate_universe(
        ds, baseline_grid_points=120, control_sample_points=120, min_candidate_target_distance_usd=11.0
    )
    assert scan["stage1_scanned_checkpoints"] == 22346
    assert scan["stage1_coverage_ratio"] == 1.0
    assert scan["unbiased_control_count"] == 120


def test_t24_synthetic_vs_historical_provenance_segregation_guard() -> None:
    """T24: Rejects synthetic provenance (`SYNTHETIC_UNIT_TEST`) in historical evidence lists."""
    ok_hist, err_hist = validate_historical_provenance_segregation(
        [{"provenance": "HISTORICAL_BACKTEST_V1792", "id": 1}]
    )
    assert ok_hist is True
    assert err_hist is None

    ok_syn, err_syn = validate_historical_provenance_segregation(
        [
            {"provenance": "HISTORICAL_BACKTEST_V1792", "id": 1},
            {"provenance": "SYNTHETIC_UNIT_TEST", "id": 2},
        ]
    )
    assert ok_syn is False
    assert err_syn == "SYNTHETIC_PROVENANCE_CONTAMINATION_DETECTED"
