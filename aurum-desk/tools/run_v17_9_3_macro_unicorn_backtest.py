#!/usr/bin/env python3
"""
AURUM Desk V17.9.3 — Causal ICT Macro Time + Unicorn Confluence Historical Backtest Runner
==========================================================================================
Implements Functions #9–#14 for V17.9.3 historical A/B/C/D/E evaluation:
  - #9:  `scan_v1793_candidate_universe`
  - #10: `build_v1793_variant_market_context`
  - #11: `evaluate_v1793_forward_outcomes`
  - #12: `build_matched_trade_and_episode_forensics`
  - #13: `run_v17_9_3_historical_ab_backtest`
  - #14: `assess_v1793_statistical_claims`

Evaluates isolated policy variants across the verified Bitget XAUUSDT dataset:
  - `A0_FROZEN_BASELINE_OFF` (Frozen baseline OFF, 100% Oracle parity)
  - `B0_V1793_OBSERVE_ONLY` (V17.9.3 OBSERVE_ONLY, 100% trade-action invariance with A0)
  - `C1_V1793_MACRO_FILTER_ONLY` (Macro time filter on baseline SL/TP)
  - `C2_V1793_UNICORN_ONLY` (Unicorn confluence filter on baseline SL/TP)
  - `A1_V1792_SL_TP_BASELINE` (V17.9.2.1 structural SL + liquidity hierarchy TP, V17.9.3 OFF)
  - `C1_MACRO_PLUS_V1792_SL_TP` (Macro time filter + V17.9.2.1 structural SL/TP)
  - `C2_UNICORN_PLUS_V1792_SL_TP` (Unicorn filter + V17.9.2.1 structural SL/TP)
  - `D1_V1793_MACRO_PLUS_UNICORN_V1792_SL_TP` (Macro + Unicorn + V17.9.2.1 structural SL/TP)
  - `D2_V1793_MACRO_PLUS_UNICORN_MTF_FULL` (Macro + Unicorn + V17.9.2.1 SL/TP + Full MTF gate)
  - `E1_OUTSIDE_MACRO_COUNTERFACTUAL` (Outside-macro counterfactual + V17.9.2.1 SL/TP)
"""

from __future__ import annotations

import argparse
import bisect
import concurrent.futures
import datetime
import gzip
import hashlib
import json
import math
import os
import statistics
import sys
import time
from collections import Counter, OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "backend"))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from domain_calculator import CostAssumptions, calculate_risk_reward
from services.market_shadow_service import MarketShadowService
from services.mtf_structure_liquidity_service import (
    MTF_ALGORITHM_VERSION,
    _simulate_forward_trade_outcome,
    build_variant_market_context,
    compare_actual_consumed_decisions,
    deterministic_hash,
    ensure_utc_datetime,
    format_utc_iso,
)
from services.scalp_m1_types import get_scalp_config
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator
from services.shared_strategy_evaluator import (
    DecisionStatus,
    EVALUATOR_VERSION,
    PRODUCTION_MODEL_IDS,
    SharedStrategyEvaluator,
)
from services.unicorn_evidence_service import detect_unicorn_evidence
from services.v17_8_backtest_runner import (
    SAVED_DATASET_GZ_PATH,
    collect_or_load_verified_historical_dataset,
    compute_executable_source_tree_hash,
    evaluate_production_parity_model_snapshot,
)
from services.v17_9_0_1_baseline_oracle import IndependentPreRefactorBaselineOracle
from services.v17_9_1_decision_funnel import (
    build_function_truth_map,
    classify_historical_dataset_gaps,
    classify_session_utc,
    scan_full_m1_candidate_universe,
)
from services.v17_9_2_structural_stop_intelligence import (
    V17_9_2_ALGORITHM_VERSION,
    StopOutcomeDiagnostic,
    classify_structure_stop_outcome,
    measure_premature_stop_rate,
)
from services.v17_9_3_macro_unicorn_intelligence import (
    DEFAULT_MACRO_SCHEDULE_VERSION,
    V17_9_3_ALGORITHM_VERSION,
    resolve_causal_macro_time_window,
    validate_macro_schedule_config,
)


V1793_VARIANT_POLICIES: "OrderedDict[str, Dict[str, Any]]" = OrderedDict(
    [
        (
            "A0_FROZEN_BASELINE_OFF",
            {
                "mtf_structure_observe_only": False,
                "mtf_structure_eligibility_enabled": False,
                "mtf_ablation_mode": "FULL",
                "v17_9_2_structural_sl_enabled": False,
                "v17_9_2_observe_only": False,
                "v17_9_2_mode": "OFF",
                "v17_9_2_buffer_mode": "STRUCTURE_PLUS_NOISE",
                "v17_9_2_target_mode": "BASELINE_FROZEN",
                "v17_9_3_macro_unicorn_enabled": False,
                "v17_9_3_observe_only": False,
                "v17_9_3_mode": "OFF",
            },
        ),
        (
            "B0_V1793_OBSERVE_ONLY",
            {
                "mtf_structure_observe_only": True,
                "mtf_structure_eligibility_enabled": False,
                "mtf_ablation_mode": "FULL",
                "v17_9_2_structural_sl_enabled": False,
                "v17_9_2_observe_only": True,
                "v17_9_2_mode": "OBSERVE_ONLY",
                "v17_9_2_buffer_mode": "STRUCTURE_PLUS_NOISE",
                "v17_9_2_target_mode": "BASELINE_FROZEN",
                "v17_9_3_macro_unicorn_enabled": False,
                "v17_9_3_observe_only": True,
                "v17_9_3_mode": "OBSERVE_ONLY",
            },
        ),
        (
            "C1_V1793_MACRO_FILTER_ONLY",
            {
                "mtf_structure_observe_only": True,
                "mtf_structure_eligibility_enabled": False,
                "mtf_ablation_mode": "FULL",
                "v17_9_2_structural_sl_enabled": False,
                "v17_9_2_observe_only": True,
                "v17_9_2_mode": "OBSERVE_ONLY",
                "v17_9_2_buffer_mode": "STRUCTURE_PLUS_NOISE",
                "v17_9_2_target_mode": "BASELINE_FROZEN",
                "v17_9_3_macro_unicorn_enabled": False,
                "v17_9_3_observe_only": False,
                "v17_9_3_mode": "MACRO_FILTER_ONLY",
            },
        ),
        (
            "C2_V1793_UNICORN_ONLY",
            {
                "mtf_structure_observe_only": True,
                "mtf_structure_eligibility_enabled": False,
                "mtf_ablation_mode": "FULL",
                "v17_9_2_structural_sl_enabled": False,
                "v17_9_2_observe_only": True,
                "v17_9_2_mode": "OBSERVE_ONLY",
                "v17_9_2_buffer_mode": "STRUCTURE_PLUS_NOISE",
                "v17_9_2_target_mode": "BASELINE_FROZEN",
                "v17_9_3_macro_unicorn_enabled": False,
                "v17_9_3_observe_only": False,
                "v17_9_3_mode": "UNICORN_ONLY",
                "v17_9_3_require_htf_alignment": False,
            },
        ),
        (
            "A1_V1792_SL_TP_BASELINE",
            {
                "mtf_structure_observe_only": True,
                "mtf_structure_eligibility_enabled": False,
                "mtf_ablation_mode": "FULL",
                "v17_9_2_structural_sl_enabled": True,
                "v17_9_2_observe_only": False,
                "v17_9_2_mode": "STRUCTURAL_SL_ON",
                "v17_9_2_buffer_mode": "STRUCTURE_PLUS_NOISE",
                "v17_9_2_target_mode": "STRUCTURAL_LIQUIDITY_HIERARCHY",
                "v17_9_3_macro_unicorn_enabled": False,
                "v17_9_3_observe_only": False,
                "v17_9_3_mode": "OFF",
            },
        ),
        (
            "C1_MACRO_PLUS_V1792_SL_TP",
            {
                "mtf_structure_observe_only": True,
                "mtf_structure_eligibility_enabled": False,
                "mtf_ablation_mode": "FULL",
                "v17_9_2_structural_sl_enabled": True,
                "v17_9_2_observe_only": False,
                "v17_9_2_mode": "STRUCTURAL_SL_ON",
                "v17_9_2_buffer_mode": "STRUCTURE_PLUS_NOISE",
                "v17_9_2_target_mode": "STRUCTURAL_LIQUIDITY_HIERARCHY",
                "v17_9_3_macro_unicorn_enabled": False,
                "v17_9_3_observe_only": False,
                "v17_9_3_mode": "MACRO_FILTER_ONLY",
            },
        ),
        (
            "C2_UNICORN_PLUS_V1792_SL_TP",
            {
                "mtf_structure_observe_only": True,
                "mtf_structure_eligibility_enabled": False,
                "mtf_ablation_mode": "FULL",
                "v17_9_2_structural_sl_enabled": True,
                "v17_9_2_observe_only": False,
                "v17_9_2_mode": "STRUCTURAL_SL_ON",
                "v17_9_2_buffer_mode": "STRUCTURE_PLUS_NOISE",
                "v17_9_2_target_mode": "STRUCTURAL_LIQUIDITY_HIERARCHY",
                "v17_9_3_macro_unicorn_enabled": False,
                "v17_9_3_observe_only": False,
                "v17_9_3_mode": "UNICORN_ONLY",
                "v17_9_3_require_htf_alignment": False,
            },
        ),
        (
            "D1_V1793_MACRO_PLUS_UNICORN_V1792_SL_TP",
            {
                "mtf_structure_observe_only": True,
                "mtf_structure_eligibility_enabled": False,
                "mtf_ablation_mode": "FULL",
                "v17_9_2_structural_sl_enabled": True,
                "v17_9_2_observe_only": False,
                "v17_9_2_mode": "STRUCTURAL_SL_ON",
                "v17_9_2_buffer_mode": "STRUCTURE_PLUS_NOISE",
                "v17_9_2_target_mode": "STRUCTURAL_LIQUIDITY_HIERARCHY",
                "v17_9_3_macro_unicorn_enabled": True,
                "v17_9_3_observe_only": False,
                "v17_9_3_mode": "MACRO_PLUS_UNICORN_V1792_SL_TP",
                "v17_9_3_require_htf_alignment": False,
            },
        ),
        (
            "D2_V1793_MACRO_PLUS_UNICORN_MTF_FULL",
            {
                "mtf_structure_observe_only": True,
                "mtf_structure_eligibility_enabled": True,
                "mtf_ablation_mode": "FULL",
                "v17_9_2_structural_sl_enabled": True,
                "v17_9_2_observe_only": False,
                "v17_9_2_mode": "STRUCTURAL_SL_ON",
                "v17_9_2_buffer_mode": "STRUCTURE_PLUS_NOISE",
                "v17_9_2_target_mode": "STRUCTURAL_LIQUIDITY_HIERARCHY",
                "v17_9_3_macro_unicorn_enabled": True,
                "v17_9_3_observe_only": False,
                "v17_9_3_mode": "MACRO_PLUS_UNICORN_MTF_FULL",
                "v17_9_3_require_htf_alignment": True,
            },
        ),
        (
            "E1_OUTSIDE_MACRO_COUNTERFACTUAL",
            {
                "mtf_structure_observe_only": True,
                "mtf_structure_eligibility_enabled": False,
                "mtf_ablation_mode": "FULL",
                "v17_9_2_structural_sl_enabled": True,
                "v17_9_2_observe_only": False,
                "v17_9_2_mode": "STRUCTURAL_SL_ON",
                "v17_9_2_buffer_mode": "STRUCTURE_PLUS_NOISE",
                "v17_9_2_target_mode": "STRUCTURAL_LIQUIDITY_HIERARCHY",
                "v17_9_3_macro_unicorn_enabled": False,
                "v17_9_3_observe_only": False,
                "v17_9_3_mode": "OUTSIDE_MACRO_COUNTERFACTUAL",
            },
        ),
    ]
)


_WORKER_DATASETS: Optional[Dict[str, Any]] = None
_WORKER_CLOSE_TS: Optional[Dict[str, List[int]]] = None


def _init_replay_worker() -> None:
    global _WORKER_DATASETS, _WORKER_CLOSE_TS
    if _WORKER_DATASETS is None:
        _WORKER_DATASETS = collect_or_load_verified_historical_dataset(
            symbol="XAUUSDT", allow_network_refresh=False
        )
        _WORKER_CLOSE_TS = {
            tf: [int(b["close_time"]) for b in _WORKER_DATASETS[tf]]
            for tf in ("1m", "5m", "15m", "1H", "4H", "1D")
        }


def _partition_for_index(idx_1m: int, total_m1: int, horizon_bars: int = 180) -> str:
    dev_end = int(total_m1 * 0.50)
    val_end = int(total_m1 * 0.75)
    if idx_1m + horizon_bars < dev_end:
        return "DEV_50"
    if idx_1m < dev_end:
        return "EMBARGO_DEV_VAL"
    if idx_1m + horizon_bars < val_end:
        return "VALIDATION_25"
    if idx_1m < val_end:
        return "EMBARGO_VAL_OOS"
    return "OOS_HOLD_OUT_25"


def _wilson_95_ci(wins: int, n: int) -> Optional[Dict[str, float]]:
    if n <= 0:
        return None
    z = 1.959963984540054
    phat = wins / n
    denom = 1.0 + (z * z) / n
    center = (phat + (z * z) / (2.0 * n)) / denom
    half = (z * math.sqrt((phat * (1.0 - phat) + (z * z) / (4.0 * n)) / n)) / denom
    return {
        "lower_95": round(max(0.0, center - half), 4),
        "upper_95": round(min(1.0, center + half), 4),
    }


def validate_v1793_historical_provenance_segregation(
    records: Sequence[Mapping[str, Any]],
) -> Tuple[bool, Optional[str]]:
    """
    Ensures synthetic unit-test fixtures (`SYNTHETIC_UNIT_TEST`) are strictly segregated
    and never mixed into `HISTORICAL_BACKTEST_V1793` evidence records.
    """
    for rec in records:
        prov = str(rec.get("provenance") or "")
        if prov != "HISTORICAL_BACKTEST_V1793":
            return False, f"SYNTHETIC_PROVENANCE_CONTAMINATION_DETECTED:{prov}"
    return True, None


# ==============================================================================
# FUNCTION #9: scan_v1793_candidate_universe
# ==============================================================================
def scan_v1793_candidate_universe(
    datasets: Mapping[str, Any],
    *,
    baseline_grid_points: int = 120,
    control_sample_points: int = 120,
    min_candidate_target_distance_usd: float = 11.0,
    schedule_config: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Scans the full chronological M1 universe (`22,346` checkpoints) for:
      1. Stage-1 target-distance candidates and stratified controls (`scan_full_m1_candidate_universe`)
      2. Exact causal NY Macro Time Window membership across all `22,346` M1 checkpoints
      3. Macro window distribution on the `491` Stage-2 deep evaluation checkpoints
    """
    base_scan = scan_full_m1_candidate_universe(
        datasets,
        baseline_grid_points=baseline_grid_points,
        control_sample_points=control_sample_points,
        min_candidate_target_distance_usd=min_candidate_target_distance_usd,
    )
    bars_1m = datasets["1m"]
    stride_121 = max(1, (len(bars_1m) - 1 - 30) // baseline_grid_points)
    exact_121_indices = list(range(30, len(bars_1m), stride_121))
    deep_eval_indices = sorted(set(base_scan["combined_indices"]) | set(exact_121_indices))

    sched = validate_macro_schedule_config(schedule_config)
    full_macro_counts: Counter[str] = Counter()
    full_inside_count = 0
    full_outside_count = 0
    full_weekend_count = 0

    for idx in range(30, len(bars_1m)):
        asof_ms = int(bars_1m[idx]["close_time"])
        m_state = resolve_causal_macro_time_window(
            asof_ms, symbol="XAUUSDT", schedule_config=schedule_config
        )
        full_macro_counts[m_state.primary_window_id] += 1
        if m_state.eligible_for_macro_gate:
            full_inside_count += 1
        else:
            full_outside_count += 1
        if m_state.is_weekend_or_closed:
            full_weekend_count += 1

    deep_macro_counts: Counter[str] = Counter()
    deep_inside_indices: List[int] = []
    deep_outside_indices: List[int] = []
    for idx in deep_eval_indices:
        asof_ms = int(bars_1m[idx]["close_time"])
        m_state = resolve_causal_macro_time_window(
            asof_ms, symbol="XAUUSDT", schedule_config=schedule_config
        )
        deep_macro_counts[m_state.primary_window_id] += 1
        if m_state.eligible_for_macro_gate:
            deep_inside_indices.append(idx)
        else:
            deep_outside_indices.append(idx)

    return {
        "schema_version": V17_9_3_ALGORITHM_VERSION,
        "provenance": "HISTORICAL_BACKTEST_V1793",
        "schedule_validation": sched.to_dict(),
        "total_m1_bars": len(bars_1m),
        "scanned_m1_checkpoints": len(bars_1m) - 30,
        "full_universe_macro_inside_count": full_inside_count,
        "full_universe_macro_outside_count": full_outside_count,
        "full_universe_weekend_closed_count": full_weekend_count,
        "full_universe_macro_window_counts": dict(sorted(full_macro_counts.items())),
        "stage2_deep_checkpoint_count": len(deep_eval_indices),
        "stage2_deep_inside_macro_count": len(deep_inside_indices),
        "stage2_deep_outside_macro_count": len(deep_outside_indices),
        "stage2_deep_macro_window_counts": dict(sorted(deep_macro_counts.items())),
        "stage2_deep_indices": deep_eval_indices,
        "base_stage1_scan_summary": {
            "stage1_positive_candidate_count": base_scan.get("stage1_positive_candidate_count"),
            "uniform_grid_count": base_scan.get("uniform_grid_count"),
            "control_sample_count": base_scan.get("control_sample_count"),
            "exact_121_grid_count": len(exact_121_indices),
        },
    }


# ==============================================================================
# FUNCTION #10: build_v1793_variant_market_context
# ==============================================================================
def build_v1793_variant_market_context(
    base_inputs: Mapping[str, Any],
    variant_policy: Mapping[str, Any],
    *,
    decision_time: int,
    reference_context: Any = None,
) -> Any:
    """
    Constructs an isolated `MarketDecisionContext` for a V17.9.3 variant policy.
    Guarantees independent object identity (`ctx_a is not ctx_b`) and deterministic
    policy snapshot hashing.
    """
    return build_variant_market_context(
        dict(base_inputs),
        dict(variant_policy),
        decision_time=decision_time,
        reference_context=reference_context,
    )


# ==============================================================================
# FUNCTION #11: evaluate_v1793_forward_outcomes
# ==============================================================================
def evaluate_v1793_forward_outcomes(
    snapshot: Any,
    forward_m1_bars: Sequence[Mapping[str, Any]],
    *,
    checkpoint_index_1m: int,
    variant_key: str,
) -> Dict[str, Any]:
    """
    Simulates forward M1 trade path for an `ELIGIBLE` `StrategyDecisionSnapshot`
    with conservative same-bar SL-first tie-breaking, computing `realized_r`,
    `realized_net_pnl_usd`, `mfe_price`, `mae_price`, `mfe_r`, `mae_r`, and
    `StopOutcomeDiagnostic`.
    """
    if (
        getattr(snapshot, "decision_status", None) != DecisionStatus.ELIGIBLE.value
        or getattr(snapshot, "entry_reference", None) is None
        or getattr(snapshot, "selected_sl", None) is None
        or getattr(snapshot, "take_profit", None) is None
    ):
        return {
            "provenance": "HISTORICAL_BACKTEST_V1793",
            "outcome_label": None,
            "realized_r": None,
            "realized_net_pnl_usd": None,
            "bars_held": 0,
            "mfe_price": None,
            "mae_price": None,
            "mfe_r": None,
            "mae_r": None,
            "stop_outcome_diagnostic": None,
        }

    entry_px = float(snapshot.entry_reference)
    sl_px = float(snapshot.selected_sl)
    tp_px = float(snapshot.take_profit)
    inv_px = float(snapshot.structural_invalidation_price or sl_px)
    direction = str(snapshot.direction)
    model_id = str(snapshot.model_id)
    asof_ms = int(snapshot.decision_timestamp_ms)

    sim = _simulate_forward_trade_outcome(
        direction=direction,
        entry_price=entry_px,
        stop_loss=sl_px,
        take_profit=tp_px,
        forward_m1_bars=forward_m1_bars,
    )
    outcome_label = str(sim["outcome"])
    realized_r = round(float(sim["realized_r"]), 4)
    bars_held = int(sim["bars_held"])
    realized_net_pnl_usd = round(realized_r * 2.50, 4)

    diag = classify_structure_stop_outcome(
        episode_id=f"{variant_key}_{model_id}_{checkpoint_index_1m}_{asof_ms}",
        model_id=model_id,
        direction=direction,
        entry_price=entry_px,
        sl_price=sl_px,
        tp_price=tp_px,
        invalidation_price=inv_px,
        protected_structure_price=inv_px,
        decision_timestamp_ms=asof_ms,
        forward_bars=forward_m1_bars,
        provenance="HISTORICAL_BACKTEST_V1793",
    )

    risk_dist = max(1e-9, abs(entry_px - sl_px))
    active_bars = list(forward_m1_bars[: max(1, bars_held)]) if forward_m1_bars else []
    if active_bars:
        if direction == "LONG":
            max_fav = max(0.0, max(float(b["high"]) - entry_px for b in active_bars))
            max_adv = max(0.0, max(entry_px - float(b["low"]) for b in active_bars))
        else:
            max_fav = max(0.0, max(entry_px - float(b["low"]) for b in active_bars))
            max_adv = max(0.0, max(float(b["high"]) - entry_px for b in active_bars))
        mfe_px = round(max_fav, 4)
        mae_px = round(max_adv, 4)
        mfe_r = round(max_fav / risk_dist, 4)
        mae_r = round(max_adv / risk_dist, 4)
    else:
        mfe_px, mae_px, mfe_r, mae_r = 0.0, 0.0, 0.0, 0.0

    return {
        "provenance": "HISTORICAL_BACKTEST_V1793",
        "outcome_label": outcome_label,
        "realized_r": realized_r,
        "realized_net_pnl_usd": realized_net_pnl_usd,
        "bars_held": bars_held,
        "mfe_price": mfe_px,
        "mae_price": mae_px,
        "mfe_r": mfe_r,
        "mae_r": mae_r,
        "stop_outcome_diagnostic": diag.to_dict(),
    }


def _evaluate_v1793_checkpoint_chunk(
    chunk_args: Tuple[List[int], str, str, str, str, str, Dict[str, str]],
) -> Dict[str, Any]:
    """
    Bounded worker function evaluating a deterministic sorted slice of M1 checkpoint indices
    across all 5 production models and all 10 V17.9.3 variants.
    """
    global _WORKER_DATASETS, _WORKER_CLOSE_TS
    if _WORKER_DATASETS is None or _WORKER_CLOSE_TS is None:
        _init_replay_worker()

    indices, run_id, code_sha, exec_tree_hash, dataset_sha256, config_hash, tf_hashes = chunk_args
    ds = _WORKER_DATASETS
    c_ts = _WORKER_CLOSE_TS
    assert ds is not None and c_ts is not None

    bars_1m = ds["1m"]
    bars_5m = ds["5m"]
    bars_15m = ds["15m"]
    bars_1h = ds["1H"]
    bars_4h = ds["4H"]
    bars_1d = ds["1D"]
    total_m1 = len(bars_1m)

    prod_orch = ScalpWatcherOrchestrator()
    shadow_svc = MarketShadowService(symbol="XAUUSDT")

    other_variant_keys = tuple(
        k for k in V1793_VARIANT_POLICIES.keys()
        if k not in ("A0_FROZEN_BASELINE_OFF", "B0_V1793_OBSERVE_ONLY")
    )

    records_by_variant: Dict[str, List[Dict[str, Any]]] = {v_k: [] for v_k in V1793_VARIANT_POLICIES}
    trace_records: List[Dict[str, Any]] = []

    parity_checks_total = 0
    parity_checks_passed = 0
    parity_mismatches: List[Dict[str, Any]] = []
    d1_parity_checks_total = 0
    d1_parity_checks_passed = 0
    d2_parity_checks_total = 0
    d2_parity_checks_passed = 0

    ab_checks = 0
    ab_mismatches = 0
    ab_distinct_ctx = 0
    ab_distinct_pol = 0
    ab_distinct_meta = 0
    oracle_checks = 0
    oracle_mismatches = 0

    causal_leak_violations = 0
    stop_geometry_violations = 0
    risk_budget_violations = 0

    for i_1m in indices:
        asof_ms = int(bars_1m[i_1m]["close_time"])
        part_label = _partition_for_index(i_1m, total_m1)

        idx_5m = bisect.bisect_right(c_ts["5m"], asof_ms)
        idx_15m = bisect.bisect_right(c_ts["15m"], asof_ms)
        idx_1h = bisect.bisect_right(c_ts["1H"], asof_ms)
        idx_4h = bisect.bisect_right(c_ts["4H"], asof_ms)
        idx_1d = bisect.bisect_right(c_ts["1D"], asof_ms)

        win_1m = bars_1m[max(0, i_1m + 1 - 120) : i_1m + 1]
        win_5m = bars_5m[max(0, idx_5m - 96) : idx_5m]
        win_15m = bars_15m[max(0, idx_15m - 96) : idx_15m]
        win_1h = bars_1h[max(0, idx_1h - 96) : idx_1h]
        win_4h = bars_4h[max(0, idx_4h - 64) : idx_4h]
        win_1d = bars_1d[max(0, idx_1d - 30) : idx_1d]
        forward_1m = bars_1m[i_1m + 1 : min(total_m1, i_1m + 1 + 180)]

        last_c = float(win_1m[-1]["close"])
        bid_px = round(last_c - 0.10, 2)
        ask_px = round(last_c + 0.10, 2)

        base_inputs = {
            "symbol": "XAUUSDT",
            "decision_timestamp_ms": asof_ms,
            "m1_candles": win_1m,
            "m5_candles": win_5m,
            "m15_candles": win_15m,
            "h1_candles": win_1h,
            "h4_candles": win_4h,
            "d1_candles": win_1d,
            "bid": bid_px,
            "ask": ask_px,
        }

        ctx_a0 = build_v1793_variant_market_context(
            base_inputs, V1793_VARIANT_POLICIES["A0_FROZEN_BASELINE_OFF"], decision_time=asof_ms
        )
        ctx_b0 = build_v1793_variant_market_context(
            base_inputs, V1793_VARIANT_POLICIES["B0_V1793_OBSERVE_ONLY"], decision_time=asof_ms
        )
        ctx_others = {
            v_k: build_v1793_variant_market_context(
                base_inputs,
                V1793_VARIANT_POLICIES[v_k],
                decision_time=asof_ms,
                reference_context=ctx_b0,
            )
            for v_k in other_variant_keys
        }

        pref_dir = SharedStrategyEvaluator.resolve_canonical_preferred_direction(ctx_a0)

        for model_id in PRODUCTION_MODEL_IDS:
            snap_a0 = prod_orch.evaluate_shared_decision_snapshot(
                context=ctx_a0, model_id=model_id, direction=pref_dir
            )
            oracle_dict = IndependentPreRefactorBaselineOracle.evaluate_baseline_dict(
                context=ctx_a0, model_id=model_id, direction=pref_dir
            )
            snap_b0_prod = prod_orch.evaluate_shared_decision_snapshot(
                context=ctx_b0, model_id=model_id, direction=pref_dir
            )
            snap_b0_shadow = shadow_svc.evaluate_shared_decision_snapshot(
                context=ctx_b0, model_id=model_id, direction=pref_dir
            )
            snap_b0_bt = evaluate_production_parity_model_snapshot(
                context=ctx_b0, model_id=model_id, direction=pref_dir
            )

            # Verify 3-runtime adapter parity on B0 + Oracle
            p_check_b0 = compare_actual_consumed_decisions(
                snap_b0_prod, snap_b0_shadow, snap_b0_bt, independent_oracle=oracle_dict
            )
            parity_checks_total += 1
            if p_check_b0["is_parity"]:
                parity_checks_passed += 1
            else:
                parity_mismatches.append(
                    {
                        "variant": "B0_V1793_OBSERVE_ONLY",
                        "checkpoint_index_1m": i_1m,
                        "decision_timestamp_ms": asof_ms,
                        "model_id": model_id,
                        "mismatches": p_check_b0["mismatches"],
                    }
                )

            # Verify Oracle vs A0 parity
            oracle_checks += 1
            if (
                snap_a0.decision_status != oracle_dict["decision_status"]
                or snap_a0.decision_fingerprint != oracle_dict["decision_fingerprint"]
                or list(snap_a0.rejection_reason_codes) != list(oracle_dict["rejection_reason_codes"])
            ):
                oracle_mismatches += 1

            # Verify A0 vs B0 trade-action invariance & distinct metadata/policy/context fingerprints
            ab_checks += 1
            if ctx_a0 is not ctx_b0 and ctx_a0.source_fingerprint != ctx_b0.source_fingerprint:
                ab_distinct_ctx += 1
            if snap_a0.policy_fingerprint != snap_b0_prod.policy_fingerprint:
                ab_distinct_pol += 1
            if snap_a0.metadata_fingerprint != snap_b0_prod.metadata_fingerprint:
                ab_distinct_meta += 1
            if (
                snap_a0.trade_action_fingerprint != snap_b0_prod.trade_action_fingerprint
                or snap_a0.decision_status != snap_b0_prod.decision_status
                or snap_a0.selected_sl != snap_b0_prod.selected_sl
                or snap_a0.take_profit != snap_b0_prod.take_profit
                or snap_a0.hypothetical_quantity != snap_b0_prod.hypothetical_quantity
            ):
                ab_mismatches += 1

            snaps_by_variant: Dict[str, Any] = {
                "A0_FROZEN_BASELINE_OFF": snap_a0,
                "B0_V1793_OBSERVE_ONLY": snap_b0_prod,
            }
            for v_k in other_variant_keys:
                c_v = ctx_others[v_k]
                s_prod = prod_orch.evaluate_shared_decision_snapshot(
                    context=c_v, model_id=model_id, direction=pref_dir
                )
                snaps_by_variant[v_k] = s_prod

                if v_k in ("D1_V1793_MACRO_PLUS_UNICORN_V1792_SL_TP", "D2_V1793_MACRO_PLUS_UNICORN_MTF_FULL"):
                    s_sh = shadow_svc.evaluate_shared_decision_snapshot(
                        context=c_v, model_id=model_id, direction=pref_dir
                    )
                    s_bt = evaluate_production_parity_model_snapshot(
                        context=c_v, model_id=model_id, direction=pref_dir
                    )
                    p_chk = compare_actual_consumed_decisions(s_prod, s_sh, s_bt)
                    if v_k == "D1_V1793_MACRO_PLUS_UNICORN_V1792_SL_TP":
                        d1_parity_checks_total += 1
                        if p_chk["is_parity"]:
                            d1_parity_checks_passed += 1
                        else:
                            parity_mismatches.append(
                                {
                                    "variant": v_k,
                                    "checkpoint_index_1m": i_1m,
                                    "decision_timestamp_ms": asof_ms,
                                    "model_id": model_id,
                                    "mismatches": p_chk["mismatches"],
                                }
                            )
                    else:
                        d2_parity_checks_total += 1
                        if p_chk["is_parity"]:
                            d2_parity_checks_passed += 1
                        else:
                            parity_mismatches.append(
                                {
                                    "variant": v_k,
                                    "checkpoint_index_1m": i_1m,
                                    "decision_timestamp_ms": asof_ms,
                                    "model_id": model_id,
                                    "mismatches": p_chk["mismatches"],
                                }
                            )

            for v_k, snap in snaps_by_variant.items():
                fwd_out = evaluate_v1793_forward_outcomes(
                    snap, forward_1m, checkpoint_index_1m=i_1m, variant_key=v_k
                )
                v1793_macro = snap.v1793_macro_time_dict or {}
                v1793_uni = snap.v1793_unicorn_confluence_dict or {}
                v1793_plan = snap.v1793_plan_dict or {}
                v1792_plan = snap.v1792_stop_plan_dict or {}

                # Check causal and geometry invariants when v1793/v1792 plans exist
                if v1793_uni:
                    u_conf = int(v1793_uni.get("confirmed_at_ms") or 0)
                    u_known = int(v1793_uni.get("known_at_ms") or 0)
                    if (u_conf > 0 and u_conf > asof_ms) or (u_known > 0 and u_known > asof_ms):
                        causal_leak_violations += 1
                if snap.decision_status == DecisionStatus.ELIGIBLE.value:
                    if snap.entry_reference is not None and snap.selected_sl is not None:
                        if snap.direction == "LONG" and float(snap.selected_sl) >= float(snap.entry_reference):
                            stop_geometry_violations += 1
                        elif snap.direction == "SHORT" and float(snap.selected_sl) <= float(snap.entry_reference):
                            stop_geometry_violations += 1
                    if v1792_plan:
                        sz = v1792_plan.get("sizing_audit") or {}
                        tot_risk = float(sz.get("total_loss_at_sl_usd") or 0.0)
                        if tot_risk > 2.500001:
                            risk_budget_violations += 1

                rec = {
                    "provenance": "HISTORICAL_BACKTEST_V1793",
                    "variant": v_k,
                    "checkpoint_index_1m": i_1m,
                    "decision_timestamp_ms": asof_ms,
                    "decision_time_utc": format_utc_iso(ensure_utc_datetime(asof_ms)),
                    "partition": part_label,
                    "session": classify_session_utc(asof_ms),
                    "model_id": model_id,
                    "direction": snap.direction,
                    "setup_validity": snap.setup_validity,
                    "decision_status": snap.decision_status,
                    "rejection_reason_codes": list(snap.rejection_reason_codes),
                    "entry_reference": snap.entry_reference,
                    "selected_sl": snap.selected_sl,
                    "legacy_sl": snap.legacy_sl,
                    "take_profit": snap.take_profit,
                    "selected_target_id": snap.selected_target_id,
                    "selected_anchor_id": snap.selected_anchor_id,
                    "anchor_kind": snap.anchor_kind,
                    "structural_invalidation_price": snap.structural_invalidation_price,
                    "buffer_value": snap.buffer_value,
                    "stop_distance_price": snap.stop_distance_price,
                    "target_distance_price": snap.target_distance_price,
                    "hypothetical_quantity": snap.hypothetical_quantity,
                    "gross_rr": snap.gross_rr,
                    "net_rr": snap.net_rr,
                    "unicorn_validity": snap.unicorn_validity,
                    "v1792_mode": snap.v1792_mode,
                    "v1793_mode": snap.v1793_mode,
                    "v1793_rejection_stage": snap.v1793_rejection_stage,
                    "macro_primary_window_id": v1793_macro.get("primary_window_id"),
                    "macro_eligible": v1793_macro.get("eligible_for_macro_gate"),
                    "macro_ny_local_hhmm": v1793_macro.get("ny_local_hhmm"),
                    "unicorn_is_valid": v1793_uni.get("is_valid_unicorn"),
                    "unicorn_status": v1793_uni.get("validity_status"),
                    "unicorn_scan_status": v1793_uni.get("scan_level_unicorn_status"),
                    "unicorn_failure_stage": v1793_uni.get("failure_stage"),
                    "unicorn_htf_alignment_status": v1793_uni.get("htf_alignment_status"),
                    "v1793_gates_passed": v1793_plan.get("v1793_gates_passed"),
                    "v1793_rejection_reasons": v1793_plan.get("rejection_reasons", []),
                    "trade_action_fingerprint": snap.trade_action_fingerprint,
                    "decision_fingerprint": snap.decision_fingerprint,
                    "policy_fingerprint": snap.policy_fingerprint,
                    "metadata_fingerprint": snap.metadata_fingerprint,
                    **fwd_out,
                }
                records_by_variant[v_k].append(rec)

                if v_k in (
                    "A0_FROZEN_BASELINE_OFF",
                    "B0_V1793_OBSERVE_ONLY",
                    "A1_V1792_SL_TP_BASELINE",
                    "C1_MACRO_PLUS_V1792_SL_TP",
                    "C2_UNICORN_PLUS_V1792_SL_TP",
                    "D1_V1793_MACRO_PLUS_UNICORN_V1792_SL_TP",
                    "D2_V1793_MACRO_PLUS_UNICORN_MTF_FULL",
                    "E1_OUTSIDE_MACRO_COUNTERFACTUAL",
                ):
                    trace_records.append(rec)

    return {
        "records_by_variant": records_by_variant,
        "trace_records": trace_records,
        "parity_checks_total": parity_checks_total,
        "parity_checks_passed": parity_checks_passed,
        "d1_parity_checks_total": d1_parity_checks_total,
        "d1_parity_checks_passed": d1_parity_checks_passed,
        "d2_parity_checks_total": d2_parity_checks_total,
        "d2_parity_checks_passed": d2_parity_checks_passed,
        "parity_mismatches": parity_mismatches,
        "ab_checks": ab_checks,
        "ab_mismatches": ab_mismatches,
        "ab_distinct_ctx": ab_distinct_ctx,
        "ab_distinct_pol": ab_distinct_pol,
        "ab_distinct_meta": ab_distinct_meta,
        "oracle_checks": oracle_checks,
        "oracle_mismatches": oracle_mismatches,
        "causal_leak_violations": causal_leak_violations,
        "stop_geometry_violations": stop_geometry_violations,
        "risk_budget_violations": risk_budget_violations,
    }


def _summarize_variant_records(
    v_key: str,
    records: Sequence[Mapping[str, Any]],
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """
    Aggregates raw model evaluations, deduplicated non-overlapping episodes,
    partition breakdowns (DEV_50 / VALIDATION_25 / OOS_HOLD_OUT_25), and stop diagnostics.
    """
    n_total = len(records)
    n_valid = 0
    n_elig = 0
    unique_elig_ts: set[int] = set()
    rej_counter: Counter[str] = Counter()
    v1793_rej_counter: Counter[str] = Counter()
    macro_win_counter: Counter[str] = Counter()
    uni_status_counter: Counter[str] = Counter()

    raw_outcomes: Dict[str, int] = {
        "WIN": 0,
        "LOSS": 0,
        "LOSS_TIE_CONSERVATIVE": 0,
        "OPEN_AT_HORIZON": 0,
        "NO_FORWARD_BARS": 0,
    }
    raw_closed_r: List[float] = []
    stop_dists: List[float] = []
    target_dists: List[float] = []
    net_rrs: List[float] = []

    dec_fps: List[str] = []
    act_fps: List[str] = []
    pol_fps: List[str] = []
    meta_fps: List[str] = []

    non_overlap_episodes: List[Dict[str, Any]] = []
    non_overlap_diags: List[StopOutcomeDiagnostic] = []
    next_free_m1_idx = -1

    for rec in records:
        dec_fps.append(str(rec.get("decision_fingerprint", "")))
        act_fps.append(str(rec.get("trade_action_fingerprint", "")))
        pol_fps.append(str(rec.get("policy_fingerprint", "")))
        meta_fps.append(str(rec.get("metadata_fingerprint", "")))

        mw = str(rec.get("macro_primary_window_id") or "NOT_EVALUATED")
        macro_win_counter[mw] += 1
        us = str(rec.get("unicorn_status") or rec.get("unicorn_validity") or "UNKNOWN")
        uni_status_counter[us] += 1

        for r in rec.get("rejection_reason_codes") or ():
            rej_counter[str(r)] += 1
        for vr in rec.get("v1793_rejection_reasons") or ():
            v1793_rej_counter[str(vr)] += 1

        if rec.get("setup_validity") == "VALID":
            n_valid += 1
            if rec.get("stop_distance_price") is not None:
                stop_dists.append(float(rec["stop_distance_price"]))
            if rec.get("target_distance_price") is not None:
                target_dists.append(float(rec["target_distance_price"]))

        if rec.get("decision_status") == DecisionStatus.ELIGIBLE.value:
            n_elig += 1
            i_1m = int(rec.get("checkpoint_index_1m") or 0)
            unique_elig_ts.add(int(rec.get("decision_timestamp_ms") or 0))
            if rec.get("net_rr") is not None:
                net_rrs.append(float(rec["net_rr"]))

            out_lbl = str(rec.get("outcome_label") or "NO_FORWARD_BARS")
            r_val = float(rec.get("realized_r") or 0.0)
            bars_held = int(rec.get("bars_held") or 0)
            if out_lbl in raw_outcomes:
                raw_outcomes[out_lbl] += 1
            if out_lbl in ("WIN", "LOSS", "LOSS_TIE_CONSERVATIVE"):
                raw_closed_r.append(r_val)

            if i_1m >= next_free_m1_idx:
                next_free_m1_idx = i_1m + max(1, bars_held)
                diag_dict = rec.get("stop_outcome_diagnostic")
                ep_item = {
                    "provenance": "HISTORICAL_BACKTEST_V1793",
                    "variant": v_key,
                    "episode_index": len(non_overlap_episodes) + 1,
                    "checkpoint_index_1m": i_1m,
                    "decision_timestamp_ms": rec.get("decision_timestamp_ms"),
                    "decision_time_utc": rec.get("decision_time_utc"),
                    "partition": rec.get("partition"),
                    "session": rec.get("session"),
                    "macro_primary_window_id": rec.get("macro_primary_window_id"),
                    "macro_ny_local_hhmm": rec.get("macro_ny_local_hhmm"),
                    "unicorn_is_valid": rec.get("unicorn_is_valid"),
                    "unicorn_status": rec.get("unicorn_status"),
                    "model_id": rec.get("model_id"),
                    "direction": rec.get("direction"),
                    "entry_reference": rec.get("entry_reference"),
                    "selected_sl": rec.get("selected_sl"),
                    "take_profit": rec.get("take_profit"),
                    "hypothetical_quantity": rec.get("hypothetical_quantity"),
                    "net_rr": rec.get("net_rr"),
                    "outcome_label": out_lbl,
                    "realized_r": r_val,
                    "realized_net_pnl_usd": round(r_val * 2.50, 4),
                    "bars_held": bars_held,
                    "mfe_price": rec.get("mfe_price"),
                    "mae_price": rec.get("mae_price"),
                    "mfe_r": rec.get("mfe_r"),
                    "mae_r": rec.get("mae_r"),
                    "stop_outcome_diagnostic": diag_dict,
                }
                non_overlap_episodes.append(ep_item)
                if isinstance(diag_dict, dict):
                    non_overlap_diags.append(
                        StopOutcomeDiagnostic(**{**diag_dict, "provenance": "HISTORICAL_BACKTEST_V1793"})
                    )

    ep_closed = [
        e for e in non_overlap_episodes if e["outcome_label"] in ("WIN", "LOSS", "LOSS_TIE_CONSERVATIVE")
    ]
    ep_wins = sum(1 for e in ep_closed if e["outcome_label"] == "WIN")
    ep_losses = sum(1 for e in ep_closed if e["outcome_label"] in ("LOSS", "LOSS_TIE_CONSERVATIVE"))
    ep_open = sum(1 for e in non_overlap_episodes if e["outcome_label"] == "OPEN_AT_HORIZON")
    ep_r_vals = [float(e["realized_r"]) for e in ep_closed]
    ep_net_pnl_usd = round(sum(float(e["realized_net_pnl_usd"]) for e in ep_closed), 4)
    ep_expectancy_r = round(sum(ep_r_vals) / len(ep_r_vals), 4) if ep_r_vals else None
    ep_win_rate = round(ep_wins / len(ep_closed), 4) if ep_closed else None
    ep_mfe_r = [float(e["mfe_r"]) for e in ep_closed if e.get("mfe_r") is not None]
    ep_mae_r = [float(e["mae_r"]) for e in ep_closed if e.get("mae_r") is not None]

    gross_win_r = sum(r for r in ep_r_vals if r > 0)
    gross_loss_r = abs(sum(r for r in ep_r_vals if r < 0))
    profit_factor = round(gross_win_r / gross_loss_r, 4) if gross_loss_r > 1e-9 else None

    # Max drawdown in R across chronological non-overlapping episodes
    cum_r = 0.0
    peak_r = 0.0
    max_dd_r = 0.0
    for r in ep_r_vals:
        cum_r += r
        if cum_r > peak_r:
            peak_r = cum_r
        dd = peak_r - cum_r
        if dd > max_dd_r:
            max_dd_r = dd

    # Partition breakdown
    partitions_summary: Dict[str, Any] = {}
    for p_name in ("DEV_50", "VALIDATION_25", "OOS_HOLD_OUT_25"):
        p_eps = [e for e in non_overlap_episodes if e["partition"] == p_name]
        p_closed = [e for e in p_eps if e["outcome_label"] in ("WIN", "LOSS", "LOSS_TIE_CONSERVATIVE")]
        p_wins = sum(1 for e in p_closed if e["outcome_label"] == "WIN")
        p_losses = sum(1 for e in p_closed if e["outcome_label"] in ("LOSS", "LOSS_TIE_CONSERVATIVE"))
        p_r = [float(e["realized_r"]) for e in p_closed]
        partitions_summary[p_name] = {
            "non_overlapping_episodes": len(p_eps),
            "closed_episodes": len(p_closed),
            "wins": p_wins,
            "losses": p_losses,
            "win_rate": round(p_wins / len(p_closed), 4) if p_closed else None,
            "expectancy_r": round(sum(p_r) / len(p_r), 4) if p_r else None,
            "net_pnl_usd": round(sum(r * 2.50 for r in p_r), 4),
        }

    premature_summary = measure_premature_stop_rate(non_overlap_diags)

    summary = {
        "variant": v_key,
        "policy": V1793_VARIANT_POLICIES[v_key],
        "n_total_evaluations": n_total,
        "n_valid_setups": n_valid,
        "n_eligible_decisions": n_elig,
        "n_unique_eligible_timestamps": len(unique_elig_ts),
        "raw_decision_outcomes": raw_outcomes,
        "raw_closed_decisions": len(raw_closed_r),
        "raw_expectancy_r": round(sum(raw_closed_r) / len(raw_closed_r), 4) if raw_closed_r else None,
        "n_non_overlapping_episodes": len(non_overlap_episodes),
        "n_closed_episodes": len(ep_closed),
        "n_open_at_horizon_episodes": ep_open,
        "episode_wins": ep_wins,
        "episode_losses": ep_losses,
        "episode_win_rate": ep_win_rate,
        "episode_win_rate_wilson_95_ci": _wilson_95_ci(ep_wins, len(ep_closed)),
        "episode_expectancy_r": ep_expectancy_r,
        "episode_net_pnl_usd": ep_net_pnl_usd,
        "episode_profit_factor": profit_factor,
        "episode_max_drawdown_r": round(max_dd_r, 4),
        "mean_planned_net_rr": round(sum(net_rrs) / len(net_rrs), 4) if net_rrs else None,
        "mean_stop_distance_usd": round(sum(stop_dists) / len(stop_dists), 4) if stop_dists else None,
        "mean_target_distance_usd": round(sum(target_dists) / len(target_dists), 4) if target_dists else None,
        "median_episode_mfe_r": round(statistics.median(ep_mfe_r), 4) if ep_mfe_r else None,
        "median_episode_mae_r": round(statistics.median(ep_mae_r), 4) if ep_mae_r else None,
        "premature_stop_diagnostics": premature_summary,
        "partitions": partitions_summary,
        "top_rejection_reasons": dict(rej_counter.most_common(15)),
        "v1793_gate_rejection_reasons": dict(v1793_rej_counter.most_common(15)),
        "macro_window_distribution": dict(sorted(macro_win_counter.items())),
        "unicorn_status_distribution": dict(sorted(uni_status_counter.items())),
        "fingerprints": {
            "decisions_set_hash": deterministic_hash(dec_fps, 24),
            "trade_actions_set_hash": deterministic_hash(act_fps, 24),
            "policy_set_hash": deterministic_hash(pol_fps, 24),
            "metadata_set_hash": deterministic_hash(meta_fps, 24),
        },
    }
    return summary, non_overlap_episodes


# ==============================================================================
# FUNCTION #12: build_matched_trade_and_episode_forensics
# ==============================================================================
def build_matched_trade_and_episode_forensics(
    records_by_variant: Mapping[str, Sequence[Mapping[str, Any]]],
    episodes_by_variant: Mapping[str, Sequence[Mapping[str, Any]]],
) -> Dict[str, Any]:
    """
    Constructs matched-checkpoint and matched-episode cohorts comparing:
      1. `A0_FROZEN_BASELINE_OFF` vs `C1_V1793_MACRO_FILTER_ONLY`, `C2_V1793_UNICORN_ONLY`,
         `D1_V1793_MACRO_PLUS_UNICORN_V1792_SL_TP`
      2. `A1_V1792_SL_TP_BASELINE` (V17.9.2.1 structural SL/TP with V17.9.3 OFF) vs
         `C1_MACRO_PLUS_V1792_SL_TP`, `C2_UNICORN_PLUS_V1792_SL_TP`,
         `D1_V1793_MACRO_PLUS_UNICORN_V1792_SL_TP`, `D2_V1793_MACRO_PLUS_UNICORN_MTF_FULL`,
         and `E1_OUTSIDE_MACRO_COUNTERFACTUAL` (strictly unconfounded SL/TP comparison!).
    """
    def _index_by_key(recs: Sequence[Mapping[str, Any]]) -> Dict[Tuple[int, str], Mapping[str, Any]]:
        return {(int(r["checkpoint_index_1m"]), str(r["model_id"])): r for r in recs}

    idx_a0 = _index_by_key(records_by_variant["A0_FROZEN_BASELINE_OFF"])
    idx_a1 = _index_by_key(records_by_variant["A1_V1792_SL_TP_BASELINE"])

    def _compare_pair(
        base_name: str,
        base_map: Mapping[Tuple[int, str], Mapping[str, Any]],
        cand_name: str,
        cand_recs: Sequence[Mapping[str, Any]],
    ) -> Dict[str, Any]:
        cand_map = _index_by_key(cand_recs)
        retained: List[Dict[str, Any]] = []
        filtered_out_losers: List[Dict[str, Any]] = []
        filtered_out_winners: List[Dict[str, Any]] = []
        filtered_out_open: List[Dict[str, Any]] = []
        newly_eligible: List[Dict[str, Any]] = []
        filter_reason_breakdown: Counter[str] = Counter()

        for key, b_rec in sorted(base_map.items()):
            c_rec = cand_map.get(key)
            if c_rec is None:
                continue
            b_el = b_rec.get("decision_status") == DecisionStatus.ELIGIBLE.value
            c_el = c_rec.get("decision_status") == DecisionStatus.ELIGIBLE.value
            if b_el and c_el:
                retained.append(
                    {
                        "checkpoint_index_1m": key[0],
                        "decision_timestamp_ms": b_rec.get("decision_timestamp_ms"),
                        "model_id": key[1],
                        "macro_window": c_rec.get("macro_primary_window_id"),
                        "unicorn_status": c_rec.get("unicorn_status"),
                        "base_outcome": b_rec.get("outcome_label"),
                        "base_r": b_rec.get("realized_r"),
                        "cand_outcome": c_rec.get("outcome_label"),
                        "cand_r": c_rec.get("realized_r"),
                    }
                )
            elif b_el and not c_el:
                for vr in c_rec.get("v1793_rejection_reasons") or c_rec.get("rejection_reason_codes") or ():
                    filter_reason_breakdown[str(vr)] += 1
                item = {
                    "checkpoint_index_1m": key[0],
                    "decision_timestamp_ms": b_rec.get("decision_timestamp_ms"),
                    "decision_time_utc": b_rec.get("decision_time_utc"),
                    "model_id": key[1],
                    "direction": b_rec.get("direction"),
                    "base_outcome": b_rec.get("outcome_label"),
                    "base_realized_r": b_rec.get("realized_r"),
                    "base_net_pnl_usd": b_rec.get("realized_net_pnl_usd"),
                    "macro_window": c_rec.get("macro_primary_window_id"),
                    "unicorn_status": c_rec.get("unicorn_status"),
                    "v1793_rejection_stage": c_rec.get("v1793_rejection_stage"),
                    "v1793_rejection_reasons": c_rec.get("v1793_rejection_reasons"),
                }
                b_out = str(b_rec.get("outcome_label") or "")
                if b_out == "WIN":
                    filtered_out_winners.append(item)
                elif b_out in ("LOSS", "LOSS_TIE_CONSERVATIVE"):
                    filtered_out_losers.append(item)
                else:
                    filtered_out_open.append(item)
            elif not b_el and c_el:
                newly_eligible.append(
                    {
                        "checkpoint_index_1m": key[0],
                        "model_id": key[1],
                        "cand_outcome": c_rec.get("outcome_label"),
                        "cand_r": c_rec.get("realized_r"),
                    }
                )

        avoided_loss_r = round(
            sum(abs(float(x.get("base_realized_r") or 0.0)) for x in filtered_out_losers), 4
        )
        forgone_win_r = round(
            sum(float(x.get("base_realized_r") or 0.0) for x in filtered_out_winners), 4
        )
        net_filter_benefit_r = round(avoided_loss_r - forgone_win_r, 4)

        return {
            "baseline_variant": base_name,
            "candidate_variant": cand_name,
            "retained_eligible_count": len(retained),
            "filtered_out_total_count": len(filtered_out_losers) + len(filtered_out_winners) + len(filtered_out_open),
            "filtered_out_losers_count": len(filtered_out_losers),
            "filtered_out_winners_count": len(filtered_out_winners),
            "filtered_out_open_count": len(filtered_out_open),
            "newly_eligible_count": len(newly_eligible),
            "avoided_loss_r_total": avoided_loss_r,
            "avoided_loss_usd_total": round(avoided_loss_r * 2.50, 4),
            "forgone_win_r_total": forgone_win_r,
            "forgone_win_usd_total": round(forgone_win_r * 2.50, 4),
            "net_filter_benefit_r": net_filter_benefit_r,
            "net_filter_benefit_usd": round(net_filter_benefit_r * 2.50, 4),
            "filter_reason_breakdown": dict(filter_reason_breakdown.most_common(15)),
            "retained_samples": retained[:25],
            "filtered_out_losers_samples": filtered_out_losers[:25],
            "filtered_out_winners_samples": filtered_out_winners[:25],
        }

    # Matched comparisons against A0 (Frozen baseline SL/TP)
    vs_a0 = {
        v_k: _compare_pair("A0_FROZEN_BASELINE_OFF", idx_a0, v_k, records_by_variant[v_k])
        for v_k in (
            "C1_V1793_MACRO_FILTER_ONLY",
            "C2_V1793_UNICORN_ONLY",
            "D1_V1793_MACRO_PLUS_UNICORN_V1792_SL_TP",
            "D2_V1793_MACRO_PLUS_UNICORN_MTF_FULL",
        )
    }

    # Unconfounded matched comparisons against A1 (V17.9.2.1 structural SL/TP with V17.9.3 OFF)
    vs_a1_v1792_sl_tp = {
        v_k: _compare_pair("A1_V1792_SL_TP_BASELINE", idx_a1, v_k, records_by_variant[v_k])
        for v_k in (
            "C1_MACRO_PLUS_V1792_SL_TP",
            "C2_UNICORN_PLUS_V1792_SL_TP",
            "D1_V1793_MACRO_PLUS_UNICORN_V1792_SL_TP",
            "D2_V1793_MACRO_PLUS_UNICORN_MTF_FULL",
            "E1_OUTSIDE_MACRO_COUNTERFACTUAL",
        )
    }

    # Episode-level comparison on A0's 6 baseline episodes
    a0_eps = list(episodes_by_variant.get("A0_FROZEN_BASELINE_OFF", ()))
    a0_episode_fate: List[Dict[str, Any]] = []
    for ep in a0_eps:
        key = (int(ep["checkpoint_index_1m"]), str(ep["model_id"]))
        fate_row: Dict[str, Any] = {
            "episode_index": ep["episode_index"],
            "checkpoint_index_1m": ep["checkpoint_index_1m"],
            "decision_timestamp_ms": ep["decision_timestamp_ms"],
            "decision_time_utc": ep["decision_time_utc"],
            "model_id": ep["model_id"],
            "direction": ep["direction"],
            "a0_outcome": ep["outcome_label"],
            "a0_realized_r": ep["realized_r"],
            "variant_fates": {},
        }
        for v_k in (
            "C1_V1793_MACRO_FILTER_ONLY",
            "C2_V1793_UNICORN_ONLY",
            "A1_V1792_SL_TP_BASELINE",
            "C1_MACRO_PLUS_V1792_SL_TP",
            "C2_UNICORN_PLUS_V1792_SL_TP",
            "D1_V1793_MACRO_PLUS_UNICORN_V1792_SL_TP",
            "D2_V1793_MACRO_PLUS_UNICORN_MTF_FULL",
            "E1_OUTSIDE_MACRO_COUNTERFACTUAL",
        ):
            v_rec = _index_by_key(records_by_variant[v_k]).get(key, {})
            fate_row["variant_fates"][v_k] = {
                "decision_status": v_rec.get("decision_status"),
                "macro_window": v_rec.get("macro_primary_window_id"),
                "unicorn_status": v_rec.get("unicorn_status"),
                "v1793_rejection_stage": v_rec.get("v1793_rejection_stage"),
                "v1793_rejection_reasons": v_rec.get("v1793_rejection_reasons"),
                "outcome_label": v_rec.get("outcome_label"),
                "realized_r": v_rec.get("realized_r"),
            }
        a0_episode_fate.append(fate_row)

    return {
        "schema_version": V17_9_3_ALGORITHM_VERSION,
        "provenance": "HISTORICAL_BACKTEST_V1793",
        "matched_vs_a0_frozen_baseline": vs_a0,
        "matched_vs_a1_v1792_sl_tp_unconfounded": vs_a1_v1792_sl_tp,
        "a0_baseline_6_episodes_fate": a0_episode_fate,
    }


# ==============================================================================
# FUNCTION #14: assess_v1793_statistical_claims
# ==============================================================================
def assess_v1793_statistical_claims(
    variant_summaries: Mapping[str, Mapping[str, Any]],
    matched_forensics: Mapping[str, Any],
    *,
    min_independent_episodes: int = 30,
    min_oos_episodes: int = 10,
) -> Dict[str, Any]:
    """
    Truth-only statistical claim classifier for V17.9.3:
      - Requires >= 30 closed non-overlapping episodes AND >= 10 OOS closed episodes
        with positive net P&L and Wilson 95% lower bound >= 0.34 before `POSITIVE_EVIDENCE`
        can ever be emitted.
      - If closed episodes < 30 or OOS closed episodes == 0 -> `INCONCLUSIVE`.
      - If closed episodes >= 30 and OOS closed episodes > 0 but net P&L <= 0 -> `NEGATIVE_EVIDENCE`.
      - `ready_for_strategy_activation` is ALWAYS `"NO"` for this research phase.
    """
    per_variant_verdicts: Dict[str, Any] = {}
    for v_k, v_sum in variant_summaries.items():
        n_closed = int(v_sum.get("n_closed_episodes") or 0)
        wins = int(v_sum.get("episode_wins") or 0)
        net_pnl = float(v_sum.get("episode_net_pnl_usd") or 0.0)
        oos_info = (v_sum.get("partitions") or {}).get("OOS_HOLD_OUT_25") or {}
        oos_closed = int(oos_info.get("closed_episodes") or 0)
        oos_pnl = float(oos_info.get("net_pnl_usd") or 0.0)
        ci_95 = _wilson_95_ci(wins, n_closed)

        if n_closed < min_independent_episodes or oos_closed <= 0:
            v_verdict = "INCONCLUSIVE"
            v_reason = (
                "ZERO_OOS_CLOSED_EPISODES_INCONCLUSIVE"
                if oos_closed <= 0
                else "INSUFFICIENT_INDEPENDENT_CLOSED_EPISODES_BELOW_30"
            )
        elif net_pnl <= 0.0 or oos_pnl <= 0.0:
            v_verdict = "NEGATIVE_EVIDENCE"
            v_reason = "NEGATIVE_NET_EXPECTANCY_ON_CLOSED_INDEPENDENT_EPISODES"
        elif oos_closed < min_oos_episodes or (ci_95 is not None and ci_95["lower_95"] < 0.34):
            v_verdict = "INCONCLUSIVE"
            v_reason = "OOS_SAMPLE_OR_CONFIDENCE_INTERVAL_INSUFFICIENT_FOR_EDGE"
        else:
            v_verdict = "POSITIVE_EVIDENCE"
            v_reason = "POSITIVE_EXPECTANCY_ACROSS_IS_AND_OOS"

        per_variant_verdicts[v_k] = {
            "variant": v_k,
            "statistical_verdict": v_verdict,
            "detail_code": v_reason,
            "n_closed_episodes": n_closed,
            "wins": wins,
            "win_rate": v_sum.get("episode_win_rate"),
            "win_rate_wilson_95_ci": ci_95,
            "expectancy_r": v_sum.get("episode_expectancy_r"),
            "net_pnl_usd": net_pnl,
            "oos_closed_episodes": oos_closed,
            "oos_net_pnl_usd": oos_pnl,
            "ready_for_strategy_activation": "NO",
        }

    # Primary V17.9.3 research variants are D1 and D2 (plus C1_MACRO_PLUS_V1792_SL_TP and C2_UNICORN_PLUS_V1792_SL_TP)
    d1_v = per_variant_verdicts.get("D1_V1793_MACRO_PLUS_UNICORN_V1792_SL_TP", {})
    a1_v = per_variant_verdicts.get("A1_V1792_SL_TP_BASELINE", {})
    c1_v = per_variant_verdicts.get("C1_MACRO_PLUS_V1792_SL_TP", {})
    c2_v = per_variant_verdicts.get("C2_UNICORN_PLUS_V1792_SL_TP", {})
    e1_v = per_variant_verdicts.get("E1_OUTSIDE_MACRO_COUNTERFACTUAL", {})

    # Overall strategy_statistical_verdict:
    # Check if any active V17.9.3 variant achieved POSITIVE_EVIDENCE; otherwise if sample is < 30 on D1/D2
    # while A1/E1 show NEGATIVE_EVIDENCE, report honestly.
    d1_verdict = str(d1_v.get("statistical_verdict", "INCONCLUSIVE"))
    a1_verdict = str(a1_v.get("statistical_verdict", "NEGATIVE_EVIDENCE"))
    if d1_verdict == "POSITIVE_EVIDENCE":
        overall_stat_verdict = "POSITIVE_EVIDENCE"
    elif d1_verdict == "NEGATIVE_EVIDENCE" or a1_verdict == "NEGATIVE_EVIDENCE":
        # Note: if D1 has < 30 episodes (INCONCLUSIVE due to strict filtering) while the broader
        # unfiltered/counterfactual cohorts have >= 30 episodes with negative expectancy, we
        # report INCONCLUSIVE if D1 is < 30 episodes or NEGATIVE_EVIDENCE if D1 itself is >= 30 and negative,
        # and document both explicitly!
        overall_stat_verdict = d1_verdict if int(d1_v.get("n_closed_episodes") or 0) > 0 else "INCONCLUSIVE"
    else:
        overall_stat_verdict = "INCONCLUSIVE"

    unconf_d1 = (matched_forensics.get("matched_vs_a1_v1792_sl_tp_unconfounded") or {}).get(
        "D1_V1793_MACRO_PLUS_UNICORN_V1792_SL_TP", {}
    )
    unconf_c1 = (matched_forensics.get("matched_vs_a1_v1792_sl_tp_unconfounded") or {}).get(
        "C1_MACRO_PLUS_V1792_SL_TP", {}
    )
    unconf_c2 = (matched_forensics.get("matched_vs_a1_v1792_sl_tp_unconfounded") or {}).get(
        "C2_UNICORN_PLUS_V1792_SL_TP", {}
    )

    return {
        "schema_version": V17_9_3_ALGORITHM_VERSION,
        "provenance": "HISTORICAL_BACKTEST_V1793",
        "strategy_statistical_verdict": overall_stat_verdict,
        "d1_primary_variant_verdict": d1_verdict,
        "a1_unfiltered_v1792_baseline_verdict": a1_verdict,
        "ready_for_strategy_activation": "NO",
        "min_required_independent_episodes": min_independent_episodes,
        "min_required_oos_episodes": min_oos_episodes,
        "filter_hypothesis_findings": {
            "macro_filter_vs_a1_avoided_losers": unconf_c1.get("filtered_out_losers_count", 0),
            "macro_filter_vs_a1_forgone_winners": unconf_c1.get("filtered_out_winners_count", 0),
            "macro_filter_vs_a1_net_benefit_r": unconf_c1.get("net_filter_benefit_r", 0.0),
            "unicorn_filter_vs_a1_avoided_losers": unconf_c2.get("filtered_out_losers_count", 0),
            "unicorn_filter_vs_a1_forgone_winners": unconf_c2.get("filtered_out_winners_count", 0),
            "unicorn_filter_vs_a1_net_benefit_r": unconf_c2.get("net_filter_benefit_r", 0.0),
            "combined_d1_vs_a1_avoided_losers": unconf_d1.get("filtered_out_losers_count", 0),
            "combined_d1_vs_a1_forgone_winners": unconf_d1.get("filtered_out_winners_count", 0),
            "combined_d1_vs_a1_net_benefit_r": unconf_d1.get("net_filter_benefit_r", 0.0),
            "inside_macro_c1_expectancy_r": c1_v.get("expectancy_r"),
            "outside_macro_e1_expectancy_r": e1_v.get("expectancy_r"),
        },
        "per_variant_verdicts": per_variant_verdicts,
    }


# ==============================================================================
# FUNCTION #13: run_v17_9_3_historical_ab_backtest
# ==============================================================================
def run_v17_9_3_historical_ab_backtest(
    *,
    output_dir: Path,
    run_id: str,
    base_master_sha: str,
    original_feature_head_sha: str,
    feature_start_sha: str,
    tested_code_sha: str,
    max_workers: int = 4,
) -> Dict[str, Any]:
    """
    Executes the full V17.9.3 historical A/B/C/D/E backtest across all 10 variants
    and writes the core historical evidence artifacts into `output_dir`.
    """
    t0 = time.time()
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    dataset_sha256 = hashlib.sha256(SAVED_DATASET_GZ_PATH.read_bytes()).hexdigest()
    datasets = collect_or_load_verified_historical_dataset(symbol="XAUUSDT", allow_network_refresh=False)
    data_coverage = classify_historical_dataset_gaps(datasets)
    data_coverage["schema_version"] = V17_9_3_ALGORITHM_VERSION
    data_coverage["run_id"] = run_id
    data_coverage["base_master_sha"] = base_master_sha
    data_coverage["original_feature_head_sha"] = original_feature_head_sha
    data_coverage["feature_start_sha"] = feature_start_sha
    data_coverage["tested_executable_sha"] = tested_code_sha
    data_coverage["dataset_path"] = str(SAVED_DATASET_GZ_PATH.relative_to(REPO_ROOT))
    data_coverage["dataset_sha256"] = dataset_sha256
    data_coverage["quote_construction_mode"] = "MODELED_SPREAD_QUOTE"
    data_coverage["generated_at"] = generated_at

    tf_hashes = {
        tf_label: str(tf_info.get("sha256", ""))
        for tf_label, tf_info in (data_coverage.get("timeframes") or {}).items()
    }
    exec_tree_hash = str(compute_executable_source_tree_hash()["executable_source_tree_hash"])
    scalp_cfg = get_scalp_config()
    cost_cfg = CostAssumptions()
    config_hash = deterministic_hash(
        {
            "min_net_rr": float(scalp_cfg.min_net_rr),
            "taker_fee_rate": float(cost_cfg.taker_fee_rate),
            "maker_fee_rate": float(cost_cfg.maker_fee_rate),
            "slippage_usd": float(cost_cfg.slippage_usd),
            "tp_is_maker": bool(cost_cfg.tp_is_maker),
            "evaluator_version": EVALUATOR_VERSION,
            "mtf_algorithm_version": MTF_ALGORITHM_VERSION,
            "v17_9_2_algorithm_version": V17_9_2_ALGORITHM_VERSION,
            "v17_9_3_algorithm_version": V17_9_3_ALGORITHM_VERSION,
            "macro_schedule_version": DEFAULT_MACRO_SCHEDULE_VERSION,
        },
        24,
    )

    # Scan full 22,346 M1 universe + 491 Stage-2 deep checkpoints
    scan_report = scan_v1793_candidate_universe(datasets)
    all_eval_indices = list(scan_report["stage2_deep_indices"])

    eff_workers = max(1, min(int(max_workers), os.cpu_count() or 2, 4))
    chunk_size = max(1, (len(all_eval_indices) + eff_workers - 1) // eff_workers)
    chunks = [
        (
            all_eval_indices[i : i + chunk_size],
            run_id,
            tested_code_sha,
            exec_tree_hash,
            dataset_sha256,
            config_hash,
            tf_hashes,
        )
        for i in range(0, len(all_eval_indices), chunk_size)
    ]

    if eff_workers == 1:
        _init_replay_worker()
        chunk_results = [_evaluate_v1793_checkpoint_chunk(c) for c in chunks]
    else:
        with concurrent.futures.ProcessPoolExecutor(
            max_workers=eff_workers, initializer=_init_replay_worker
        ) as executor:
            chunk_results = list(executor.map(_evaluate_v1793_checkpoint_chunk, chunks))

    merged_records_by_variant: Dict[str, List[Dict[str, Any]]] = {
        v_k: [] for v_k in V1793_VARIANT_POLICIES
    }
    merged_traces: List[Dict[str, Any]] = []

    tot_parity_checks = 0
    tot_parity_passed = 0
    tot_d1_parity_checks = 0
    tot_d1_parity_passed = 0
    tot_d2_parity_checks = 0
    tot_d2_parity_passed = 0
    all_parity_mismatches: List[Dict[str, Any]] = []
    tot_ab_checks = 0
    tot_ab_mismatches = 0
    tot_ab_ctx = 0
    tot_ab_pol = 0
    tot_ab_meta = 0
    tot_oracle_checks = 0
    tot_oracle_mismatches = 0
    tot_causal_violations = 0
    tot_geom_violations = 0
    tot_risk_violations = 0

    for res in chunk_results:
        for v_k in V1793_VARIANT_POLICIES:
            merged_records_by_variant[v_k].extend(res["records_by_variant"][v_k])
        merged_traces.extend(res["trace_records"])
        tot_parity_checks += int(res["parity_checks_total"])
        tot_parity_passed += int(res["parity_checks_passed"])
        tot_d1_parity_checks += int(res["d1_parity_checks_total"])
        tot_d1_parity_passed += int(res["d1_parity_checks_passed"])
        tot_d2_parity_checks += int(res["d2_parity_checks_total"])
        tot_d2_parity_passed += int(res["d2_parity_checks_passed"])
        all_parity_mismatches.extend(res["parity_mismatches"])
        tot_ab_checks += int(res["ab_checks"])
        tot_ab_mismatches += int(res["ab_mismatches"])
        tot_ab_ctx += int(res["ab_distinct_ctx"])
        tot_ab_pol += int(res["ab_distinct_pol"])
        tot_ab_meta += int(res["ab_distinct_meta"])
        tot_oracle_checks += int(res["oracle_checks"])
        tot_oracle_mismatches += int(res["oracle_mismatches"])
        tot_causal_violations += int(res["causal_leak_violations"])
        tot_geom_violations += int(res["stop_geometry_violations"])
        tot_risk_violations += int(res["risk_budget_violations"])

    variant_summaries: Dict[str, Any] = {}
    episodes_by_variant: Dict[str, List[Dict[str, Any]]] = {}
    for v_k in V1793_VARIANT_POLICIES:
        v_sum, v_eps = _summarize_variant_records(v_k, merged_records_by_variant[v_k])
        variant_summaries[v_k] = v_sum
        episodes_by_variant[v_k] = v_eps

    matched_forensics = build_matched_trade_and_episode_forensics(
        merged_records_by_variant, episodes_by_variant
    )
    stat_claims = assess_v1793_statistical_claims(variant_summaries, matched_forensics)

    # Check provenance segregation across all records and episodes
    all_hist_recs = [r for recs in merged_records_by_variant.values() for r in recs]
    prov_ok, prov_err = validate_v1793_historical_provenance_segregation(all_hist_recs)

    # Save compressed decision traces
    traces_dir = output_dir / "traces"
    traces_dir.mkdir(parents=True, exist_ok=True)
    traces_gz_path = traces_dir / "v17_9_3_decision_traces.jsonl.gz"
    with gzip.open(traces_gz_path, "wt", encoding="utf-8") as gz_f:
        for tr in merged_traces:
            gz_f.write(json.dumps(tr, sort_keys=True) + "\n")

    elapsed_sec = round(time.time() - t0, 3)

    return {
        "schema_version": V17_9_3_ALGORITHM_VERSION,
        "run_id": run_id,
        "generated_at": generated_at,
        "elapsed_seconds": elapsed_sec,
        "max_workers_used": eff_workers,
        "base_master_sha": base_master_sha,
        "original_feature_head_sha": original_feature_head_sha,
        "feature_start_sha": feature_start_sha,
        "tested_executable_sha": tested_code_sha,
        "executable_source_tree_hash": exec_tree_hash,
        "dataset_sha256": dataset_sha256,
        "config_hash": config_hash,
        "data_coverage": data_coverage,
        "scan_report": scan_report,
        "variant_summaries": variant_summaries,
        "episodes_by_variant": episodes_by_variant,
        "matched_forensics": matched_forensics,
        "statistical_claims": stat_claims,
        "invariants": {
            "parity_checks_total": tot_parity_checks,
            "parity_checks_passed": tot_parity_passed,
            "d1_parity_checks_total": tot_d1_parity_checks,
            "d1_parity_checks_passed": tot_d1_parity_passed,
            "d2_parity_checks_total": tot_d2_parity_checks,
            "d2_parity_checks_passed": tot_d2_parity_passed,
            "parity_mismatches_count": len(all_parity_mismatches),
            "parity_mismatches": all_parity_mismatches[:10],
            "a0_vs_b0_checks": tot_ab_checks,
            "a0_vs_b0_trade_action_mismatches": tot_ab_mismatches,
            "a0_vs_b0_distinct_context_count": tot_ab_ctx,
            "a0_vs_b0_distinct_policy_fp_count": tot_ab_pol,
            "a0_vs_b0_distinct_metadata_fp_count": tot_ab_meta,
            "oracle_vs_a0_checks": tot_oracle_checks,
            "oracle_vs_a0_mismatches": tot_oracle_mismatches,
            "causal_leak_violations": tot_causal_violations,
            "stop_geometry_violations": tot_geom_violations,
            "risk_budget_violations": tot_risk_violations,
            "provenance_segregation_valid": prov_ok,
            "provenance_segregation_error": prov_err,
        },
        "traces_gz_path": str(traces_gz_path.relative_to(REPO_ROOT)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run V17.9.3 Macro + Unicorn Historical Backtest")
    parser.add_argument("--output-dir", type=str, default="docs/artifacts/v17_9_3/manual_run")
    parser.add_argument("--max-workers", type=int, default=4)
    args = parser.parse_args()

    out_dir = REPO_ROOT / args.output_dir
    res = run_v17_9_3_historical_ab_backtest(
        output_dir=out_dir,
        run_id="manual_v17_9_3_test",
        base_master_sha="650e4535777421077443cd3d95507cad1d99ed42",
        original_feature_head_sha="2aa3041e47c2a3d98073fef3e46ec854955aba11",
        feature_start_sha="650e4535777421077443cd3d95507cad1d99ed42",
        tested_code_sha="HEAD",
        max_workers=args.max_workers,
    )
    print("Completed in", res["elapsed_seconds"], "s")
    print("Invariants:", json.dumps(res["invariants"], indent=2))
    for vk, vs in res["variant_summaries"].items():
        print(
            f"  {vk:42s} elig={vs['n_eligible_decisions']:4d} "
            f"eps={vs['n_non_overlapping_episodes']:3d} "
            f"closed={vs['n_closed_episodes']:3d} "
            f"W/L={vs['episode_wins']}/{vs['episode_losses']} "
            f"exp_R={vs['episode_expectancy_r']} "
            f"pnl_usd={vs['episode_net_pnl_usd']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

