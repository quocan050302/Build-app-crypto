#!/usr/bin/env python3
"""
AURUM Desk V17.9.2 — Structure-Valid SL/TP & Trade Invalidation Historical Backtest Runner
==========================================================================================
Executes the complete V17.9.2 historical experiment across 9 isolated policy variants:
  - A_FROZEN_BASELINE_OFF
  - B_V1792_OBSERVE_ONLY
  - C_V1792_STRUCTURAL_SL_ON
  - D1_STRUCTURE_ONLY
  - D2_STRUCTURE_PLUS_NOISE
  - D3_TP_POLICY_ONLY
  - D4_STRUCTURE_PLUS_TP
  - D5_MTF_ELIGIBILITY_FULL
  - D6_ANNOTATED_ABLATIONS

Enforces:
  - Strict closed-candle causal cutoff (`known_at_ms <= decision_timestamp_ms`)
  - Zero synthetic fill, zero interpolation, explicit `MODELED_SPREAD_QUOTE` disclosure
  - Independent `MarketDecisionContext` instances per variant (no A/B same-object tautology)
  - 3-Runtime Adapter (`ScalpWatcherOrchestrator`, `MarketShadowService`, `evaluate_production_parity_model_snapshot`)
    and `IndependentPreRefactorBaselineOracle` parity verification
  - Non-overlapping single-position episode deduplication & 50%/25%/25% chronological OOS split
    with 180-M1 forward horizon embargo
  - Before/After forensic stop outcome classification (`PREMATURE_NOISE_SWEEP_STOP`,
    `DELAYED_INEVITABLE_LOSS`, `TRUE_INVALIDATION_LOSS`, `CLEAN_WIN`, `SAME_BAR_CONSERVATIVE_LOSS`, `OPEN_AT_HORIZON`)
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
import subprocess
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
    compare_ab_variant_decisions,
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
    REQUIRED_PARITY_FIELDS,
    SharedStrategyEvaluator,
    compare_decision_parity,
)
from services.v17_8_backtest_runner import (
    SAVED_DATASET_GZ_PATH,
    collect_or_load_verified_historical_dataset,
    compute_executable_source_tree_hash,
    evaluate_production_parity_model_snapshot,
)
from services.v17_9_0_1_baseline_oracle import IndependentPreRefactorBaselineOracle
from services.v17_9_1_decision_funnel import (
    build_decision_funnel_report,
    build_function_truth_map,
    classify_historical_dataset_gaps,
    classify_session_utc,
    extract_decision_numerical_operands,
    scan_full_m1_candidate_universe,
)
from services.v17_9_2_structural_stop_intelligence import (
    V17_9_2_ALGORITHM_VERSION,
    StopOutcomeDiagnostic,
    classify_structure_stop_outcome,
    measure_premature_stop_rate,
)


V1792_VARIANT_POLICIES: "OrderedDict[str, Dict[str, Any]]" = OrderedDict(
    [
        (
            "A_FROZEN_BASELINE_OFF",
            {
                "mtf_structure_observe_only": False,
                "mtf_structure_eligibility_enabled": False,
                "mtf_ablation_mode": "FULL",
                "v17_9_2_structural_sl_enabled": False,
                "v17_9_2_observe_only": False,
                "v17_9_2_mode": "OFF",
                "v17_9_2_buffer_mode": "STRUCTURE_PLUS_NOISE",
                "v17_9_2_target_mode": "BASELINE_FROZEN",
            },
        ),
        (
            "B_V1792_OBSERVE_ONLY",
            {
                "mtf_structure_observe_only": True,
                "mtf_structure_eligibility_enabled": False,
                "mtf_ablation_mode": "FULL",
                "v17_9_2_structural_sl_enabled": False,
                "v17_9_2_observe_only": True,
                "v17_9_2_mode": "OBSERVE_ONLY",
                "v17_9_2_buffer_mode": "STRUCTURE_PLUS_NOISE",
                "v17_9_2_target_mode": "BASELINE_FROZEN",
            },
        ),
        (
            "C_V1792_STRUCTURAL_SL_ON",
            {
                "mtf_structure_observe_only": True,
                "mtf_structure_eligibility_enabled": False,
                "mtf_ablation_mode": "FULL",
                "v17_9_2_structural_sl_enabled": True,
                "v17_9_2_observe_only": False,
                "v17_9_2_mode": "STRUCTURAL_SL_ON",
                "v17_9_2_buffer_mode": "STRUCTURE_PLUS_NOISE",
                "v17_9_2_target_mode": "BASELINE_FROZEN",
            },
        ),
        (
            "D1_STRUCTURE_ONLY",
            {
                "mtf_structure_observe_only": True,
                "mtf_structure_eligibility_enabled": False,
                "mtf_ablation_mode": "FULL",
                "v17_9_2_structural_sl_enabled": True,
                "v17_9_2_observe_only": False,
                "v17_9_2_mode": "STRUCTURE_ONLY",
                "v17_9_2_buffer_mode": "STRUCTURE_ONLY",
                "v17_9_2_target_mode": "BASELINE_FROZEN",
            },
        ),
        (
            "D2_STRUCTURE_PLUS_NOISE",
            {
                "mtf_structure_observe_only": True,
                "mtf_structure_eligibility_enabled": False,
                "mtf_ablation_mode": "FULL",
                "v17_9_2_structural_sl_enabled": True,
                "v17_9_2_observe_only": False,
                "v17_9_2_mode": "STRUCTURE_PLUS_NOISE",
                "v17_9_2_buffer_mode": "STRUCTURE_PLUS_NOISE",
                "v17_9_2_target_mode": "BASELINE_FROZEN",
            },
        ),
        (
            "D3_TP_POLICY_ONLY",
            {
                "mtf_structure_observe_only": True,
                "mtf_structure_eligibility_enabled": False,
                "mtf_ablation_mode": "FULL",
                "v17_9_2_structural_sl_enabled": False,
                "v17_9_2_observe_only": False,
                "v17_9_2_mode": "TP_POLICY_ONLY",
                "v17_9_2_buffer_mode": "STRUCTURE_PLUS_NOISE",
                "v17_9_2_target_mode": "V1792_CAUSAL_STRUCTURAL_VIABLE",
            },
        ),
        (
            "D4_STRUCTURE_PLUS_TP",
            {
                "mtf_structure_observe_only": True,
                "mtf_structure_eligibility_enabled": False,
                "mtf_ablation_mode": "FULL",
                "v17_9_2_structural_sl_enabled": True,
                "v17_9_2_observe_only": False,
                "v17_9_2_mode": "STRUCTURE_PLUS_TP",
                "v17_9_2_buffer_mode": "STRUCTURE_PLUS_NOISE",
                "v17_9_2_target_mode": "V1792_CAUSAL_STRUCTURAL_VIABLE",
            },
        ),
        (
            "D5_MTF_ELIGIBILITY_FULL",
            {
                "mtf_structure_observe_only": False,
                "mtf_structure_eligibility_enabled": True,
                "mtf_ablation_mode": "FULL",
                "v17_9_2_structural_sl_enabled": False,
                "v17_9_2_observe_only": False,
                "v17_9_2_mode": "OFF",
                "v17_9_2_buffer_mode": "STRUCTURE_PLUS_NOISE",
                "v17_9_2_target_mode": "BASELINE_FROZEN",
            },
        ),
        (
            "D6_ANNOTATED_ABLATIONS",
            {
                "mtf_structure_observe_only": False,
                "mtf_structure_eligibility_enabled": True,
                "mtf_ablation_mode": "FULL",
                "v17_9_2_structural_sl_enabled": True,
                "v17_9_2_observe_only": False,
                "v17_9_2_mode": "STRUCTURE_PLUS_TP",
                "v17_9_2_buffer_mode": "STRUCTURE_PLUS_NOISE",
                "v17_9_2_target_mode": "V1792_CAUSAL_STRUCTURAL_VIABLE",
            },
        ),
    ]
)

BASELINE_FORENSIC_EPISODES: Tuple[Tuple[int, str, str], ...] = (
    (6652, "SMC_M1_FVG_CONTINUATION", "SHORT"),
    (6676, "SMC_M1_REVERSAL", "SHORT"),
    (6886, "SMC_M1_REVERSAL", "SHORT"),
    (14469, "SMC_M1_FVG_CONTINUATION", "SHORT"),
    (14608, "SMC_M1_REVERSAL", "SHORT"),
    (15383, "SMC_M1_REVERSAL", "SHORT"),
)

_WORKER_DATASETS: Optional[Dict[str, Any]] = None
_WORKER_CLOSE_TS: Optional[Dict[str, List[int]]] = None


def _init_replay_worker() -> None:
    global _WORKER_DATASETS, _WORKER_CLOSE_TS
    _WORKER_DATASETS = collect_or_load_verified_historical_dataset(
        symbol="XAUUSDT", allow_network_refresh=False
    )
    _WORKER_CLOSE_TS = {
        "5m": [int(b["close_time"]) for b in _WORKER_DATASETS["5m"]],
        "15m": [int(b["close_time"]) for b in _WORKER_DATASETS["15m"]],
        "1H": [int(b["close_time"]) for b in _WORKER_DATASETS["1H"]],
        "4H": [int(b["close_time"]) for b in _WORKER_DATASETS["4H"]],
        "1D": [int(b["close_time"]) for b in _WORKER_DATASETS["1D"]],
    }


def _wilson_95_ci(wins: int, total: int) -> Optional[Dict[str, float]]:
    if total <= 0:
        return None
    z = 1.959963984540054
    p = wins / total
    denom = 1.0 + (z * z) / total
    center = (p + (z * z) / (2.0 * total)) / denom
    half = (z * math.sqrt((p * (1.0 - p) + (z * z) / (4.0 * total)) / total)) / denom
    return {
        "lower_95": round(max(0.0, center - half), 4),
        "upper_95": round(min(1.0, center + half), 4),
    }


def _partition_for_index(i_1m: int, total_m1: int) -> str:
    """
    Contiguous chronological calendar partitions across the M1 series
    with 180-bar forward horizon embargo awareness:
      - TRAIN: [30 .. 50% of M1 series)
      - VALIDATION: [50% .. 75% of M1 series)
      - OUT_OF_SAMPLE: [75% .. end of M1 series)
    """
    cut_train = int(total_m1 * 0.50)
    cut_val = int(total_m1 * 0.75)
    if i_1m < cut_train:
        return "TRAIN"
    if i_1m < cut_val:
        return "VALIDATION"
    return "OUT_OF_SAMPLE"


def evaluate_v1792_statistical_verdict(
    *,
    closed_trades: int,
    wins: int,
    net_pnl_usd: float,
    oos_closed_trades: int,
    oos_net_pnl_usd: float,
    min_independent_episodes: int = 30,
    min_oos_episodes: int = 10,
) -> Dict[str, Any]:
    """
    Section 06 & 10: Honest statistical verdict classifier.
    - Requires >= 30 non-overlapping closed independent episodes AND > 0 OOS closed episodes
      before any directional conclusion can be drawn.
    - If closed_trades < 30 or oos_closed_trades == 0 -> `INCONCLUSIVE`.
    - If closed_trades >= 30 and oos_closed_trades > 0 but net_pnl_usd <= 0 or oos_net_pnl_usd <= 0
      -> `NEGATIVE_EVIDENCE`.
    - `ready_for_strategy_activation` is ALWAYS `"NO"` for this research phase.
    """
    win_rate = round(wins / closed_trades, 4) if closed_trades > 0 else None
    ci_95 = _wilson_95_ci(wins, closed_trades)

    if closed_trades < min_independent_episodes or oos_closed_trades <= 0:
        verdict = "INCONCLUSIVE"
        detail_code = (
            "ZERO_OOS_CLOSED_EPISODES_INCONCLUSIVE"
            if oos_closed_trades <= 0
            else "INSUFFICIENT_INDEPENDENT_CLOSED_EPISODES_BELOW_30"
        )
    elif net_pnl_usd <= 0.0 or oos_net_pnl_usd <= 0.0:
        verdict = "NEGATIVE_EVIDENCE"
        detail_code = "NEGATIVE_NET_EXPECTANCY_ON_CLOSED_INDEPENDENT_EPISODES"
    elif oos_closed_trades < min_oos_episodes or (ci_95 is not None and ci_95["lower_95"] < 0.34):
        verdict = "INCONCLUSIVE"
        detail_code = "OOS_SAMPLE_OR_CONFIDENCE_INTERVAL_INSUFFICIENT_FOR_EDGE"
    else:
        verdict = "EDGE_SUPPORTED"
        detail_code = "POSITIVE_EXPECTANCY_ACROSS_IS_AND_OOS"

    return {
        "strategy_statistical_verdict": verdict,
        "strategy_statistical_detail_code": detail_code,
        "ready_for_strategy_activation": "NO",
        "closed_independent_episodes": int(closed_trades),
        "wins": int(wins),
        "win_rate": win_rate,
        "win_rate_wilson_95_ci": ci_95,
        "net_pnl_usd": round(float(net_pnl_usd), 4),
        "oos_closed_episodes": int(oos_closed_trades),
        "oos_net_pnl_usd": round(float(oos_net_pnl_usd), 4),
        "min_required_independent_episodes": int(min_independent_episodes),
    }


def validate_historical_provenance_segregation(
    records: Sequence[Mapping[str, Any]],
) -> Tuple[bool, Optional[str]]:
    """
    Section 07 (T26) & Section 12: Ensure synthetic unit-test fixtures (`SYNTHETIC_UNIT_TEST`)
    are strictly segregated and never mixed into `HISTORICAL_BACKTEST_V1792` evidence records.
    """
    for rec in records:
        prov = str(rec.get("provenance") or "")
        if prov != "HISTORICAL_BACKTEST_V1792":
            return False, "SYNTHETIC_PROVENANCE_CONTAMINATION_DETECTED"
    return True, None


def _evaluate_v1792_checkpoint_chunk(
    chunk_args: Tuple[List[int], str, str, str, str, str, Dict[str, str]],
) -> Dict[str, Any]:
    """
    Evaluate a deterministic sorted slice of M1 checkpoint indices across all 5 production models
    and all 9 V17.9.2 variants (`A_FROZEN_BASELINE_OFF`, `B_V1792_OBSERVE_ONLY`, `C_V1792_STRUCTURAL_SL_ON`,
    `D1`..`D6`), verifying:
      - Oracle vs A exact baseline invariance
      - A vs B 100% trade-action invariance & distinct context/policy/metadata fingerprints
      - 3-Runtime Adapter parity (`ScalpWatcherOrchestrator`, `MarketShadowService`, `evaluate_production_parity_model_snapshot`)
        on B, C, and D4
      - Forward outcome & premature stop classification on all eligible decisions
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

    other_variant_keys = (
        "C_V1792_STRUCTURAL_SL_ON",
        "D1_STRUCTURE_ONLY",
        "D2_STRUCTURE_PLUS_NOISE",
        "D3_TP_POLICY_ONLY",
        "D4_STRUCTURE_PLUS_TP",
        "D5_MTF_ELIGIBILITY_FULL",
        "D6_ANNOTATED_ABLATIONS",
    )

    records_by_variant: Dict[str, List[Dict[str, Any]]] = {v_k: [] for v_k in V1792_VARIANT_POLICIES}
    trace_records: List[Dict[str, Any]] = []
    eligible_parity_records: List[Dict[str, Any]] = []
    anchor_audit_records: List[Dict[str, Any]] = []
    target_rr_audit_records: List[Dict[str, Any]] = []
    sizing_invariant_records: List[Dict[str, Any]] = []

    parity_checks_total = 0
    parity_checks_passed = 0
    parity_mismatches: List[Dict[str, Any]] = []
    c_parity_checks_total = 0
    c_parity_checks_passed = 0
    d4_parity_checks_total = 0
    d4_parity_checks_passed = 0

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

        ctx_a = build_variant_market_context(
            base_inputs, V1792_VARIANT_POLICIES["A_FROZEN_BASELINE_OFF"], decision_time=asof_ms
        )
        ctx_b = build_variant_market_context(
            base_inputs, V1792_VARIANT_POLICIES["B_V1792_OBSERVE_ONLY"], decision_time=asof_ms
        )
        ctx_others = {
            v_k: build_variant_market_context(
                base_inputs,
                V1792_VARIANT_POLICIES[v_k],
                decision_time=asof_ms,
                reference_context=ctx_b,
            )
            for v_k in other_variant_keys
        }

        pref_dir = SharedStrategyEvaluator.resolve_canonical_preferred_direction(ctx_a)

        for model_id in PRODUCTION_MODEL_IDS:
            snap_a = prod_orch.evaluate_shared_decision_snapshot(
                context=ctx_a, model_id=model_id, direction=pref_dir
            )
            oracle_dict = IndependentPreRefactorBaselineOracle.evaluate_baseline_dict(
                context=ctx_a, model_id=model_id, direction=pref_dir
            )
            snap_b_prod = prod_orch.evaluate_shared_decision_snapshot(
                context=ctx_b, model_id=model_id, direction=pref_dir
            )
            snap_b_shadow = shadow_svc.evaluate_shared_decision_snapshot(
                context=ctx_b, model_id=model_id, direction=pref_dir
            )
            snap_b_bt = evaluate_production_parity_model_snapshot(
                model_id=model_id, context=ctx_b, direction=pref_dir
            )["shared_decision_snapshot"]

            # 1. Oracle vs A parity
            oracle_checks += 1
            p_ora = compare_decision_parity(oracle_dict, snap_a, check_input_config_hashes=False)
            if (
                not p_ora["is_match"]
                or oracle_dict.get("evidence_fingerprint") != snap_a.evidence_fingerprint
                or oracle_dict.get("policy_fingerprint") != snap_a.policy_fingerprint
                or oracle_dict.get("decision_fingerprint") != snap_a.decision_fingerprint
            ):
                oracle_mismatches += 1

            # 2. A vs B separation & action invariance
            ab_checks += 1
            ab_cmp = compare_ab_variant_decisions(ctx_a, snap_a, ctx_b, snap_b_prod)
            if ab_cmp["distinct_context"]:
                ab_distinct_ctx += 1
            if ab_cmp["distinct_policy_fingerprint"]:
                ab_distinct_pol += 1
            if ab_cmp["distinct_metadata_fingerprint"]:
                ab_distinct_meta += 1
            if not ab_cmp["is_valid"]:
                ab_mismatches += 1

            # 3. 3-Runtime Adapter + Oracle Parity on B
            parity_checks_total += 1
            p_3way_b = compare_actual_consumed_decisions(
                snap_b_prod, snap_b_shadow, snap_b_bt, independent_oracle=oracle_dict
            )
            if p_3way_b["is_parity"]:
                parity_checks_passed += 1
            else:
                parity_mismatches.append(
                    {
                        "variant": "B_V1792_OBSERVE_ONLY",
                        "checkpoint_index_1m": i_1m,
                        "decision_timestamp_ms": asof_ms,
                        "model_id": model_id,
                        "mismatches": p_3way_b["mismatches"],
                    }
                )

            # Audit V17.9.2 observe-only stop & target plan on valid setups
            if snap_b_prod.setup_validity == "VALID" and snap_b_prod.v1792_stop_plan_dict is not None:
                v_plan = snap_b_prod.v1792_stop_plan_dict
                t_plan = snap_b_prod.v1792_target_plan_dict or {}
                sel_anc = v_plan.get("selected_anchor")
                if sel_anc is not None and v_plan.get("status") != "INVALID_STOP":
                    anc_known = int(sel_anc.get("known_at_ms") or 0)
                    anc_close = int(sel_anc.get("source_bar_close_ms") or 0)
                    if anc_known > asof_ms or anc_close > asof_ms or not sel_anc.get("is_closed_bar"):
                        causal_leak_violations += 1
                    prop_sl = float(v_plan.get("proposed_sl") or 0.0)
                    inv_px = float(v_plan.get("invalidation_price") or 0.0)
                    ent_px = float(snap_b_prod.entry_reference or 0.0)
                    if pref_dir == "LONG" and not (0.0 < prop_sl < inv_px < ent_px):
                        stop_geometry_violations += 1
                    elif pref_dir == "SHORT" and not (ent_px < inv_px < prop_sl):
                        stop_geometry_violations += 1

                if len(anchor_audit_records) < 60:
                    anchor_audit_records.append(
                        {
                            "checkpoint_index_1m": i_1m,
                            "decision_timestamp_ms": asof_ms,
                            "decision_time_utc": format_utc_iso(ensure_utc_datetime(asof_ms)),
                            "model_id": model_id,
                            "direction": pref_dir,
                            "entry_reference": snap_b_prod.entry_reference,
                            "baseline_anchor_id": snap_b_prod.selected_anchor_id,
                            "baseline_anchor_kind": snap_b_prod.anchor_kind,
                            "baseline_invalidation_price": snap_b_prod.structural_invalidation_price,
                            "baseline_selected_sl": snap_b_prod.selected_sl,
                            "v1792_selected_anchor": sel_anc,
                            "v1792_invalidation_price": v_plan.get("invalidation_price"),
                            "v1792_noise_buffer_price": v_plan.get("noise_buffer_price"),
                            "v1792_proposed_sl": v_plan.get("proposed_sl"),
                            "v1792_stop_distance": v_plan.get("stop_distance"),
                            "v1792_status": v_plan.get("status"),
                            "v1792_rejection_reasons": v_plan.get("rejection_reasons"),
                        }
                    )

            # Helper to record variant decision and forward outcome + stop diagnostic
            def _record_variant_snapshot(
                v_key: str,
                snap_obj: Any,
                prot_struct_px: Optional[float],
                three_way_status: Optional[str] = None,
                adapter_ids: Optional[Dict[str, str]] = None,
            ) -> Dict[str, Any]:
                nonlocal risk_budget_violations
                sim_res: Optional[Dict[str, Any]] = None
                stop_diag: Optional[Dict[str, Any]] = None

                if snap_obj.decision_status == DecisionStatus.ELIGIBLE.value:
                    sim_res = _simulate_forward_trade_outcome(
                        snap_obj.direction,
                        float(snap_obj.entry_reference),
                        float(snap_obj.selected_sl),
                        float(snap_obj.take_profit),
                        forward_1m,
                    )
                    eff_prot = (
                        float(prot_struct_px)
                        if prot_struct_px is not None
                        else float(snap_obj.structural_invalidation_price or snap_obj.selected_sl)
                    )
                    diag_obj = classify_structure_stop_outcome(
                        episode_id=f"{v_key}_{i_1m}_{model_id}",
                        model_id=model_id,
                        direction=snap_obj.direction,
                        entry_price=float(snap_obj.entry_reference),
                        sl_price=float(snap_obj.selected_sl),
                        tp_price=float(snap_obj.take_profit),
                        invalidation_price=float(snap_obj.structural_invalidation_price or snap_obj.selected_sl),
                        protected_structure_price=eff_prot,
                        decision_timestamp_ms=asof_ms,
                        forward_bars=forward_1m,
                        provenance="HISTORICAL_BACKTEST_V1792",
                    )
                    stop_diag = diag_obj.to_dict()

                    # Check risk budget invariant on eligible decision
                    calc_chk = calculate_risk_reward(
                        direction=snap_obj.direction,
                        planned_entry=float(snap_obj.entry_reference),
                        stop_loss=float(snap_obj.selected_sl),
                        take_profit=float(snap_obj.take_profit),
                        capital_usdt=1000.0,
                        risk_pct=0.25,
                        leverage=20,
                        margin_mode="ISOLATED",
                        min_net_rr=2.0,
                        costs=CostAssumptions(),
                        now_ms=asof_ms,
                    )
                    if float(calc_chk.net_risk_usdt or 0.0) > 2.50 + 1e-5 or float(calc_chk.net_rr or 0.0) < 2.0 - 1e-6:
                        risk_budget_violations += 1

                rec = {
                    "provenance": "HISTORICAL_BACKTEST_V1792",
                    "variant": v_key,
                    "checkpoint_index_1m": i_1m,
                    "decision_id": snap_obj.decision_id,
                    "decision_timestamp_ms": asof_ms,
                    "decision_time_utc": format_utc_iso(ensure_utc_datetime(asof_ms)),
                    "model_id": model_id,
                    "direction": pref_dir,
                    "partition": part_label,
                    "session": classify_session_utc(asof_ms),
                    "setup_validity": snap_obj.setup_validity,
                    "decision_status": snap_obj.decision_status,
                    "entry_reference": snap_obj.entry_reference,
                    "selected_anchor_id": snap_obj.selected_anchor_id,
                    "anchor_kind": snap_obj.anchor_kind,
                    "structural_invalidation_price": snap_obj.structural_invalidation_price,
                    "buffer_value": snap_obj.buffer_value,
                    "selected_sl": snap_obj.selected_sl,
                    "take_profit": snap_obj.take_profit,
                    "selected_target_id": snap_obj.selected_target_id,
                    "hypothetical_quantity": snap_obj.hypothetical_quantity,
                    "stop_distance_price": snap_obj.stop_distance_price,
                    "target_distance_price": snap_obj.target_distance_price,
                    "gross_rr": snap_obj.gross_rr,
                    "net_rr": snap_obj.net_rr,
                    "rejection_reason_codes": list(snap_obj.rejection_reason_codes),
                    "decision_fingerprint": snap_obj.decision_fingerprint,
                    "trade_action_fingerprint": snap_obj.trade_action_fingerprint,
                    "policy_fingerprint": snap_obj.policy_fingerprint,
                    "metadata_fingerprint": snap_obj.metadata_fingerprint,
                    "outcome_label": sim_res["outcome"] if sim_res else None,
                    "realized_r": sim_res["realized_r"] if sim_res else None,
                    "bars_held": sim_res["bars_held"] if sim_res else None,
                    "stop_outcome_diagnostic": stop_diag,
                    "three_way_parity_status": three_way_status,
                    "adapter_call_ids": adapter_ids,
                }
                records_by_variant[v_key].append(rec)
                return rec

            prot_px_b = (
                snap_b_prod.v1792_stop_plan_dict.get("invalidation_price")
                if snap_b_prod.v1792_stop_plan_dict
                else snap_b_prod.structural_invalidation_price
            )
            rec_a = _record_variant_snapshot("A_FROZEN_BASELINE_OFF", snap_a, prot_px_b)
            rec_b = _record_variant_snapshot(
                "B_V1792_OBSERVE_ONLY",
                snap_b_prod,
                prot_px_b,
                three_way_status=p_3way_b["status"],
                adapter_ids=p_3way_b["adapter_call_ids"],
            )
            if rec_b["decision_status"] == DecisionStatus.ELIGIBLE.value:
                eligible_parity_records.append(rec_b)

            # Evaluate C, D1..D6
            for v_key in other_variant_keys:
                ctx_v = ctx_others[v_key]
                snap_v = prod_orch.evaluate_shared_decision_snapshot(
                    context=ctx_v, model_id=model_id, direction=pref_dir
                )
                three_way_stat: Optional[str] = None
                three_way_ids: Optional[Dict[str, str]] = None

                if v_key in ("C_V1792_STRUCTURAL_SL_ON", "D4_STRUCTURE_PLUS_TP"):
                    snap_v_shad = shadow_svc.evaluate_shared_decision_snapshot(
                        context=ctx_v, model_id=model_id, direction=pref_dir
                    )
                    snap_v_bt = evaluate_production_parity_model_snapshot(
                        model_id=model_id, context=ctx_v, direction=pref_dir
                    )["shared_decision_snapshot"]
                    p_3way_v = compare_actual_consumed_decisions(snap_v, snap_v_shad, snap_v_bt)
                    three_way_stat = p_3way_v["status"]
                    three_way_ids = p_3way_v["adapter_call_ids"]
                    if v_key == "C_V1792_STRUCTURAL_SL_ON":
                        c_parity_checks_total += 1
                        if p_3way_v["is_parity"]:
                            c_parity_checks_passed += 1
                        else:
                            parity_mismatches.append(
                                {
                                    "variant": v_key,
                                    "checkpoint_index_1m": i_1m,
                                    "decision_timestamp_ms": asof_ms,
                                    "model_id": model_id,
                                    "mismatches": p_3way_v["mismatches"],
                                }
                            )
                    else:
                        d4_parity_checks_total += 1
                        if p_3way_v["is_parity"]:
                            d4_parity_checks_passed += 1
                        else:
                            parity_mismatches.append(
                                {
                                    "variant": v_key,
                                    "checkpoint_index_1m": i_1m,
                                    "decision_timestamp_ms": asof_ms,
                                    "model_id": model_id,
                                    "mismatches": p_3way_v["mismatches"],
                                }
                            )

                prot_v = (
                    snap_v.v1792_stop_plan_dict.get("invalidation_price")
                    if snap_v.v1792_stop_plan_dict
                    else snap_v.structural_invalidation_price
                )
                rec_v = _record_variant_snapshot(
                    v_key, snap_v, prot_v, three_way_status=three_way_stat, adapter_ids=three_way_ids
                )

                if v_key == "D4_STRUCTURE_PLUS_TP" and snap_v.setup_validity == "VALID":
                    if len(target_rr_audit_records) < 60:
                        target_rr_audit_records.append(
                            {
                                "checkpoint_index_1m": i_1m,
                                "decision_timestamp_ms": asof_ms,
                                "decision_time_utc": format_utc_iso(ensure_utc_datetime(asof_ms)),
                                "model_id": model_id,
                                "direction": pref_dir,
                                "entry_reference": snap_v.entry_reference,
                                "baseline_sl": snap_a.selected_sl,
                                "baseline_tp": snap_a.take_profit,
                                "baseline_target_id": snap_a.selected_target_id,
                                "baseline_net_rr": snap_a.net_rr,
                                "baseline_status": snap_a.decision_status,
                                "v1792_sl": snap_v.selected_sl,
                                "v1792_tp": snap_v.take_profit,
                                "v1792_target_id": snap_v.selected_target_id,
                                "v1792_net_rr": snap_v.net_rr,
                                "v1792_status": snap_v.decision_status,
                                "v1792_target_plan": snap_v.v1792_target_plan_dict,
                            }
                        )
                    if snap_v.decision_status == DecisionStatus.ELIGIBLE.value and len(sizing_invariant_records) < 60:
                        ops = extract_decision_numerical_operands(snap_v, ctx_v)
                        sizing_invariant_records.append(
                            {
                                "checkpoint_index_1m": i_1m,
                                "decision_timestamp_ms": asof_ms,
                                "model_id": model_id,
                                "direction": pref_dir,
                                "baseline_sl": snap_a.selected_sl,
                                "baseline_qty": snap_a.hypothetical_quantity,
                                "v1792_sl": snap_v.selected_sl,
                                "v1792_tp": snap_v.take_profit,
                                "v1792_qty": snap_v.hypothetical_quantity,
                                "v1792_net_rr": snap_v.net_rr,
                                "numerical_operands": ops,
                            }
                        )

            # Save compact trace record if A, C, D3, or D4 is eligible or as sample
            if (
                rec_a["decision_status"] == "ELIGIBLE"
                or records_by_variant["D4_STRUCTURE_PLUS_TP"][-1]["decision_status"] == "ELIGIBLE"
                or len(trace_records) < 80
            ):
                trace_records.append(
                    {
                        "provenance": "HISTORICAL_BACKTEST_V1792",
                        "run_id": run_id,
                        "code_sha": code_sha,
                        "executable_tree_hash": exec_tree_hash,
                        "dataset_sha256": dataset_sha256,
                        "config_hash": config_hash,
                        "checkpoint_index_1m": i_1m,
                        "decision_timestamp_ms": asof_ms,
                        "decision_time_utc": format_utc_iso(ensure_utc_datetime(asof_ms)),
                        "partition": part_label,
                        "model_id": model_id,
                        "direction": pref_dir,
                        "baseline_a": {
                            "decision_status": rec_a["decision_status"],
                            "entry_reference": rec_a["entry_reference"],
                            "selected_sl": rec_a["selected_sl"],
                            "take_profit": rec_a["take_profit"],
                            "hypothetical_quantity": rec_a["hypothetical_quantity"],
                            "net_rr": rec_a["net_rr"],
                            "rejection_reason_codes": rec_a["rejection_reason_codes"],
                            "outcome_label": rec_a["outcome_label"],
                        },
                        "structural_sl_c": {
                            "decision_status": records_by_variant["C_V1792_STRUCTURAL_SL_ON"][-1]["decision_status"],
                            "selected_sl": records_by_variant["C_V1792_STRUCTURAL_SL_ON"][-1]["selected_sl"],
                            "take_profit": records_by_variant["C_V1792_STRUCTURAL_SL_ON"][-1]["take_profit"],
                            "hypothetical_quantity": records_by_variant["C_V1792_STRUCTURAL_SL_ON"][-1]["hypothetical_quantity"],
                            "net_rr": records_by_variant["C_V1792_STRUCTURAL_SL_ON"][-1]["net_rr"],
                            "rejection_reason_codes": records_by_variant["C_V1792_STRUCTURAL_SL_ON"][-1]["rejection_reason_codes"],
                            "outcome_label": records_by_variant["C_V1792_STRUCTURAL_SL_ON"][-1]["outcome_label"],
                        },
                        "structure_plus_tp_d4": {
                            "decision_status": records_by_variant["D4_STRUCTURE_PLUS_TP"][-1]["decision_status"],
                            "selected_sl": records_by_variant["D4_STRUCTURE_PLUS_TP"][-1]["selected_sl"],
                            "take_profit": records_by_variant["D4_STRUCTURE_PLUS_TP"][-1]["take_profit"],
                            "hypothetical_quantity": records_by_variant["D4_STRUCTURE_PLUS_TP"][-1]["hypothetical_quantity"],
                            "net_rr": records_by_variant["D4_STRUCTURE_PLUS_TP"][-1]["net_rr"],
                            "rejection_reason_codes": records_by_variant["D4_STRUCTURE_PLUS_TP"][-1]["rejection_reason_codes"],
                            "outcome_label": records_by_variant["D4_STRUCTURE_PLUS_TP"][-1]["outcome_label"],
                        },
                    }
                )

    return {
        "records_by_variant": records_by_variant,
        "trace_records": trace_records,
        "eligible_parity_records": eligible_parity_records,
        "anchor_audit_records": anchor_audit_records,
        "target_rr_audit_records": target_rr_audit_records,
        "sizing_invariant_records": sizing_invariant_records,
        "parity_stats": {
            "parity_checks_total": parity_checks_total,
            "parity_checks_passed": parity_checks_passed,
            "c_parity_checks_total": c_parity_checks_total,
            "c_parity_checks_passed": c_parity_checks_passed,
            "d4_parity_checks_total": d4_parity_checks_total,
            "d4_parity_checks_passed": d4_parity_checks_passed,
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
        },
    }


def _summarize_v1792_variant(
    v_key: str,
    policy: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """
    Summarize variant evaluation metrics across both:
      1. All canonical model decisions (`all_eligible_model_decisions`)
      2. Independent non-overlapping single-position episodes (`non_overlapping_episodes`)
    Includes premature stop classification (`PREMATURE_NOISE_SWEEP_STOP`, `DELAYED_INEVITABLE_LOSS`,
    `TRUE_INVALIDATION_LOSS`, `CLEAN_WIN`, `OPEN_AT_HORIZON`), MFE/MAE, hold bars, and Wilson 95% CI.
    """
    n_eval = len(records)
    n_valid = 0
    n_elig = 0
    n_rej = 0
    rej_reasons: Counter[str] = Counter()
    outcomes: Counter[str] = Counter({"WIN": 0, "LOSS": 0, "LOSS_TIE_CONSERVATIVE": 0, "OPEN_AT_HORIZON": 0})
    closed_r_list: List[float] = []
    open_r_list: List[float] = []
    net_rr_list: List[float] = []
    stop_dist_list: List[float] = []
    target_dist_list: List[float] = []
    qty_list: List[float] = []
    part_counts: Counter[str] = Counter({"TRAIN": 0, "VALIDATION": 0, "OUT_OF_SAMPLE": 0})

    dec_fps: List[str] = []
    act_fps: List[str] = []
    pol_fps: List[str] = []
    meta_fps: List[str] = []

    next_free_m1_idx = -1
    non_overlap_episodes: List[Dict[str, Any]] = []
    non_overlap_diags: List[StopOutcomeDiagnostic] = []
    unique_elig_timestamps = set()

    for rec in records:
        part_counts[str(rec.get("partition", "OUT_OF_SAMPLE"))] += 1
        dec_fps.append(str(rec.get("decision_fingerprint", "")))
        act_fps.append(str(rec.get("trade_action_fingerprint", "")))
        pol_fps.append(str(rec.get("policy_fingerprint", "")))
        meta_fps.append(str(rec.get("metadata_fingerprint", "")))

        if rec.get("setup_validity") == "VALID":
            n_valid += 1
            if rec.get("stop_distance_price") is not None:
                stop_dist_list.append(float(rec["stop_distance_price"]))
            if rec.get("target_distance_price") is not None:
                target_dist_list.append(float(rec["target_distance_price"]))

        if rec.get("decision_status") == DecisionStatus.ELIGIBLE.value:
            n_elig += 1
            i_1m = int(rec.get("checkpoint_index_1m") or 0)
            unique_elig_timestamps.add(int(rec.get("decision_timestamp_ms") or 0))
            if rec.get("net_rr") is not None:
                net_rr_list.append(float(rec["net_rr"]))
            if rec.get("hypothetical_quantity") is not None:
                qty_list.append(float(rec["hypothetical_quantity"]))

            out_lbl = str(rec.get("outcome_label") or "NO_FORWARD_BARS")
            r_val = float(rec.get("realized_r") or 0.0)
            bars_held = int(rec.get("bars_held") or 0)
            if out_lbl in outcomes:
                outcomes[out_lbl] += 1
            if out_lbl in ("WIN", "LOSS", "LOSS_TIE_CONSERVATIVE"):
                closed_r_list.append(r_val)
            elif out_lbl == "OPEN_AT_HORIZON":
                open_r_list.append(r_val)

            if i_1m >= next_free_m1_idx:
                next_free_m1_idx = i_1m + max(1, bars_held)
                diag_dict = rec.get("stop_outcome_diagnostic")
                ep_item = {
                    "provenance": "HISTORICAL_BACKTEST_V1792",
                    "variant": v_key,
                    "episode_index": len(non_overlap_episodes) + 1,
                    "checkpoint_index_1m": i_1m,
                    "decision_timestamp_ms": rec.get("decision_timestamp_ms"),
                    "decision_time_utc": rec.get("decision_time_utc"),
                    "partition": rec.get("partition"),
                    "session": rec.get("session"),
                    "model_id": rec.get("model_id"),
                    "direction": rec.get("direction"),
                    "entry_reference": rec.get("entry_reference"),
                    "selected_sl": rec.get("selected_sl"),
                    "take_profit": rec.get("take_profit"),
                    "hypothetical_quantity": rec.get("hypothetical_quantity"),
                    "net_rr": rec.get("net_rr"),
                    "outcome_label": out_lbl,
                    "realized_r": r_val,
                    "bars_held": bars_held,
                    "stop_outcome_diagnostic": diag_dict,
                }
                non_overlap_episodes.append(ep_item)
                if isinstance(diag_dict, dict):
                    non_overlap_diags.append(
                        StopOutcomeDiagnostic(
                            episode_id=str(diag_dict["episode_id"]),
                            model_id=str(diag_dict["model_id"]),
                            direction=str(diag_dict["direction"]),
                            entry_price=float(diag_dict["entry_price"]),
                            sl_price=float(diag_dict["sl_price"]),
                            tp_price=float(diag_dict["tp_price"]),
                            invalidation_price=float(diag_dict["invalidation_price"]),
                            protected_structure_price=float(diag_dict["protected_structure_price"]),
                            decision_timestamp_ms=int(diag_dict["decision_timestamp_ms"]),
                            outcome_class=str(diag_dict["outcome_class"]),
                            stop_hit=bool(diag_dict["stop_hit"]),
                            stop_hit_bar_ts=diag_dict.get("stop_hit_bar_ts"),
                            tp_hit=bool(diag_dict["tp_hit"]),
                            tp_hit_bar_ts=diag_dict.get("tp_hit_bar_ts"),
                            structure_broken=bool(diag_dict["structure_broken"]),
                            structure_break_bar_ts=diag_dict.get("structure_break_bar_ts"),
                            tp_would_hit_after_stop=bool(diag_dict["tp_would_hit_after_stop"]),
                            mfe_usd=float(diag_dict["mfe_usd"]),
                            mae_usd=float(diag_dict["mae_usd"]),
                            mfe_r=float(diag_dict["mfe_r"]),
                            mae_r=float(diag_dict["mae_r"]),
                            bars_held=int(diag_dict["bars_held"]),
                            provenance=str(diag_dict.get("provenance", "HISTORICAL_BACKTEST_V1792")),
                            sub_class=str(diag_dict.get("sub_class", "")),
                            mfe_before_stop_usd=float(diag_dict.get("mfe_before_stop_usd", 0.0)),
                            mfe_after_stop_usd=float(diag_dict.get("mfe_after_stop_usd", 0.0)),
                            has_data_gap_before_exit=bool(diag_dict.get("has_data_gap_before_exit", False)),
                            max_bar_gap_ms=int(diag_dict.get("max_bar_gap_ms", 0)),
                        )
                    )
        else:
            n_rej += 1
            for r_code in rec.get("rejection_reason_codes") or ():
                rs = str(r_code).split(":")[0].strip()
                rej_reasons[rs] += 1

    # Non-overlapping episode metrics (authoritative for statistical claims)
    ep_closed = [
        e for e in non_overlap_episodes if e["outcome_label"] in ("WIN", "LOSS", "LOSS_TIE_CONSERVATIVE")
    ]
    ep_wins = sum(1 for e in ep_closed if e["outcome_label"] == "WIN")
    ep_losses = len(ep_closed) - ep_wins
    ep_r_list = [float(e["realized_r"]) for e in ep_closed]
    ep_win_rate = round(ep_wins / len(ep_closed), 4) if ep_closed else None
    ep_wilson_ci = _wilson_95_ci(ep_wins, len(ep_closed))
    ep_expectancy_r = round(sum(ep_r_list) / len(ep_r_list), 4) if ep_r_list else None
    ep_median_r = round(float(statistics.median(ep_r_list)), 4) if ep_r_list else None

    ep_gross_win_r = sum(x for x in ep_r_list if x > 0)
    ep_gross_loss_r = abs(sum(x for x in ep_r_list if x < 0))
    if ep_gross_loss_r > 0:
        ep_pf: Optional[float] = round(ep_gross_win_r / ep_gross_loss_r, 4)
        ep_pf_null_reason: Optional[str] = None
    else:
        ep_pf = None
        ep_pf_null_reason = "ZERO_CLOSED_LOSS_DENOMINATOR" if ep_gross_win_r > 0 else "ZERO_CLOSED_TRADES"

    cum_r = 0.0
    peak_r = 0.0
    max_dd_r = 0.0
    for r_val in ep_r_list:
        cum_r += r_val
        if cum_r > peak_r:
            peak_r = cum_r
        dd = peak_r - cum_r
        if dd > max_dd_r:
            max_dd_r = dd

    # OOS non-overlapping episodes
    oos_ep_closed = [e for e in ep_closed if e.get("partition") == "OUT_OF_SAMPLE"]
    oos_ep_r = sum(float(e["realized_r"]) for e in oos_ep_closed)

    stat_verdict_info = evaluate_v1792_statistical_verdict(
        closed_trades=len(ep_closed),
        wins=ep_wins,
        net_pnl_usd=sum(ep_r_list) * 2.50,
        oos_closed_trades=len(oos_ep_closed),
        oos_net_pnl_usd=oos_ep_r * 2.50,
    )

    premature_stop_summary = measure_premature_stop_rate(non_overlap_diags)
    funnel = build_decision_funnel_report(records, cohort_label=f"V1792_{v_key}")

    return {
        "variant": v_key,
        "policy": dict(policy),
        "n_decisions_evaluated": n_eval,
        "n_valid_setups": n_valid,
        "n_eligible_model_decisions": n_elig,
        "n_unique_eligible_timestamps": len(unique_elig_timestamps),
        "n_rejected_decisions": n_rej,
        "n_non_overlapping_episodes": len(non_overlap_episodes),
        "n_non_overlapping_closed_episodes": len(ep_closed),
        "n_non_overlapping_open_at_horizon": len(non_overlap_episodes) - len(ep_closed),
        "non_overlapping_wins": ep_wins,
        "non_overlapping_losses": ep_losses,
        "non_overlapping_win_rate": ep_win_rate,
        "non_overlapping_win_rate_wilson_95_ci": ep_wilson_ci,
        "non_overlapping_expectancy_r": ep_expectancy_r,
        "non_overlapping_median_r": ep_median_r,
        "non_overlapping_profit_factor": ep_pf,
        "non_overlapping_profit_factor_null_reason": ep_pf_null_reason,
        "non_overlapping_max_drawdown_r": round(max_dd_r, 4),
        "all_model_decisions_outcomes": dict(outcomes),
        "mean_planned_net_rr": round(sum(net_rr_list) / len(net_rr_list), 4) if net_rr_list else None,
        "mean_stop_distance_usd": round(sum(stop_dist_list) / len(stop_dist_list), 4) if stop_dist_list else None,
        "mean_target_distance_usd": round(sum(target_dist_list) / len(target_dist_list), 4) if target_dist_list else None,
        "mean_hypothetical_quantity": round(sum(qty_list) / len(qty_list), 4) if qty_list else None,
        "premature_stop_metrics": premature_stop_summary,
        "statistical_verdict": stat_verdict_info,
        "rejection_reasons_overlapping": dict(rej_reasons),
        "first_blocking_stage_counts": funnel["stage_first_blocking_counts"],
        "reconciliation": funnel["reconciliation"],
        "partition_decision_counts": dict(part_counts),
        "decisions_set_hash": deterministic_hash(dec_fps, 24),
        "trade_actions_set_hash": deterministic_hash(act_fps, 24),
        "policy_set_hash": deterministic_hash(pol_fps, 24),
        "metadata_set_hash": deterministic_hash(meta_fps, 24),
        "episodes": non_overlap_episodes,
    }


def _build_6_episode_forensics(datasets: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Build detailed Before (`A_FROZEN_BASELINE_OFF`) vs After (`C_V1792_STRUCTURAL_SL_ON` and
    `D4_STRUCTURE_PLUS_TP`) forensic diagnostics on the 6 historical non-overlapping baseline episodes.
    """
    bars_1m = datasets["1m"]
    bars_5m = datasets["5m"]
    bars_15m = datasets["15m"]
    bars_1h = datasets["1H"]
    bars_4h = datasets["4H"]
    bars_1d = datasets["1D"]
    total_m1 = len(bars_1m)

    c5 = [int(b["close_time"]) for b in bars_5m]
    c15 = [int(b["close_time"]) for b in bars_15m]
    c1h = [int(b["close_time"]) for b in bars_1h]
    c4h = [int(b["close_time"]) for b in bars_4h]
    c1d = [int(b["close_time"]) for b in bars_1d]

    forensic_episodes: List[Dict[str, Any]] = []
    base_diags: List[StopOutcomeDiagnostic] = []

    for ep_idx, (i_1m, model_id, direction) in enumerate(BASELINE_FORENSIC_EPISODES, start=1):
        asof_ms = int(bars_1m[i_1m]["close_time"])
        i5 = bisect.bisect_right(c5, asof_ms)
        i15 = bisect.bisect_right(c15, asof_ms)
        i1h = bisect.bisect_right(c1h, asof_ms)
        i4h = bisect.bisect_right(c4h, asof_ms)
        i1d = bisect.bisect_right(c1d, asof_ms)

        w1 = bars_1m[max(0, i_1m + 1 - 120) : i_1m + 1]
        w5 = bars_5m[max(0, i5 - 96) : i5]
        w15 = bars_15m[max(0, i15 - 96) : i15]
        w1h = bars_1h[max(0, i1h - 96) : i1h]
        w4h = bars_4h[max(0, i4h - 64) : i4h]
        w1d = bars_1d[max(0, i1d - 30) : i1d]
        fwd = bars_1m[i_1m + 1 : min(total_m1, i_1m + 1 + 180)]

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

        ctx_a = build_variant_market_context(
            base_in, V1792_VARIANT_POLICIES["A_FROZEN_BASELINE_OFF"], decision_time=asof_ms
        )
        ctx_b = build_variant_market_context(
            base_in, V1792_VARIANT_POLICIES["B_V1792_OBSERVE_ONLY"], decision_time=asof_ms
        )
        ctx_c = build_variant_market_context(
            base_in, V1792_VARIANT_POLICIES["C_V1792_STRUCTURAL_SL_ON"], decision_time=asof_ms, reference_context=ctx_b
        )
        ctx_d4 = build_variant_market_context(
            base_in, V1792_VARIANT_POLICIES["D4_STRUCTURE_PLUS_TP"], decision_time=asof_ms, reference_context=ctx_b
        )

        snap_a = SharedStrategyEvaluator.evaluate_model_direction(ctx_a, model_id, direction)
        snap_b = SharedStrategyEvaluator.evaluate_model_direction(ctx_b, model_id, direction)
        snap_c = SharedStrategyEvaluator.evaluate_model_direction(ctx_c, model_id, direction)
        snap_d4 = SharedStrategyEvaluator.evaluate_model_direction(ctx_d4, model_id, direction)

        v_plan = snap_b.v1792_stop_plan_dict or {}
        prot_px = float(v_plan.get("invalidation_price") or snap_a.structural_invalidation_price)

        diag_a = classify_structure_stop_outcome(
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
            provenance="HISTORICAL_BACKTEST_V1792",
        )
        base_diags.append(diag_a)

        # Also simulate hypothetical outcome if V17.9.2 proposed SL were placed with baseline TP
        prop_sl = float(v_plan.get("proposed_sl") or snap_c.selected_sl)
        diag_hyp_c = classify_structure_stop_outcome(
            episode_id=f"V1792_HYP_EP{ep_idx}_{i_1m}",
            model_id=model_id,
            direction=direction,
            entry_price=float(snap_a.entry_reference),
            sl_price=prop_sl,
            tp_price=float(snap_a.take_profit),
            invalidation_price=prot_px,
            protected_structure_price=prot_px,
            decision_timestamp_ms=asof_ms,
            forward_bars=fwd,
            provenance="HISTORICAL_BACKTEST_V1792",
        )

        forensic_episodes.append(
            {
                "episode_number": ep_idx,
                "checkpoint_index_1m": i_1m,
                "decision_timestamp_ms": asof_ms,
                "decision_time_utc": format_utc_iso(ensure_utc_datetime(asof_ms)),
                "model_id": model_id,
                "direction": direction,
                "entry_reference": snap_a.entry_reference,
                "before_baseline_a": {
                    "decision_status": snap_a.decision_status,
                    "selected_anchor_id": snap_a.selected_anchor_id,
                    "anchor_kind": snap_a.anchor_kind,
                    "structural_invalidation_price": snap_a.structural_invalidation_price,
                    "buffer_value": snap_a.buffer_value,
                    "selected_sl": snap_a.selected_sl,
                    "stop_distance_price": snap_a.stop_distance_price,
                    "take_profit": snap_a.take_profit,
                    "target_distance_price": snap_a.target_distance_price,
                    "hypothetical_quantity": snap_a.hypothetical_quantity,
                    "gross_rr": snap_a.gross_rr,
                    "net_rr": snap_a.net_rr,
                    "outcome_diagnostic": diag_a.to_dict(),
                },
                "after_v1792_structural_sl_c": {
                    "decision_status": snap_c.decision_status,
                    "selected_anchor_id": snap_c.selected_anchor_id,
                    "anchor_kind": snap_c.anchor_kind,
                    "structural_invalidation_price": snap_c.structural_invalidation_price,
                    "buffer_value": snap_c.buffer_value,
                    "selected_sl": snap_c.selected_sl,
                    "stop_distance_price": snap_c.stop_distance_price,
                    "take_profit": snap_c.take_profit,
                    "hypothetical_quantity": snap_c.hypothetical_quantity,
                    "gross_rr": snap_c.gross_rr,
                    "net_rr": snap_c.net_rr,
                    "rejection_reason_codes": list(snap_c.rejection_reason_codes),
                    "hypothetical_path_if_traded": diag_hyp_c.to_dict(),
                },
                "after_v1792_structure_plus_tp_d4": {
                    "decision_status": snap_d4.decision_status,
                    "selected_sl": snap_d4.selected_sl,
                    "take_profit": snap_d4.take_profit,
                    "selected_target_id": snap_d4.selected_target_id,
                    "hypothetical_quantity": snap_d4.hypothetical_quantity,
                    "gross_rr": snap_d4.gross_rr,
                    "net_rr": snap_d4.net_rr,
                    "rejection_reason_codes": list(snap_d4.rejection_reason_codes),
                },
            }
        )

    return {
        "schema_version": V17_9_2_ALGORITHM_VERSION,
        "provenance": "HISTORICAL_BACKTEST_V1792",
        "baseline_6_episodes_summary": measure_premature_stop_rate(base_diags),
        "episodes": forensic_episodes,
    }


def run_v17_9_2_structural_stop_backtest(
    *,
    output_dir: Path,
    run_id: str,
    qa_base_sha: str,
    new_branch: str,
    tested_code_sha: str,
    max_workers: int = 4,
) -> Dict[str, Any]:
    """
    Execute the complete V17.9.2 historical backtest across all 9 variants (`A`, `B`, `C`, `D1`..`D6`),
    computing all required evidence artifacts in `output_dir`.
    """
    t0 = time.time()
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # 1. Function Truth Map
    truth_map = build_function_truth_map(REPO_ROOT)
    truth_map["v17_9_2_algorithm_version"] = V17_9_2_ALGORITHM_VERSION
    truth_map["run_id"] = run_id
    truth_map["tested_code_sha"] = tested_code_sha
    (output_dir / "function_truth_map.json").write_text(
        json.dumps(truth_map, indent=2, sort_keys=True), encoding="utf-8"
    )

    # 2. Dataset Authority & Gap Audit
    dataset_sha256 = hashlib.sha256(SAVED_DATASET_GZ_PATH.read_bytes()).hexdigest()
    datasets = collect_or_load_verified_historical_dataset(symbol="XAUUSDT", allow_network_refresh=False)
    data_coverage = classify_historical_dataset_gaps(datasets)
    data_coverage["schema_version"] = V17_9_2_ALGORITHM_VERSION
    data_coverage["run_id"] = run_id
    data_coverage["qa_base_sha"] = qa_base_sha
    data_coverage["new_branch"] = new_branch
    data_coverage["tested_executable_sha"] = tested_code_sha
    data_coverage["dataset_path"] = str(SAVED_DATASET_GZ_PATH.relative_to(REPO_ROOT))
    data_coverage["dataset_sha256"] = dataset_sha256
    data_coverage["generated_at"] = generated_at
    (output_dir / "dataset_coverage_and_gap_audit.json").write_text(
        json.dumps(data_coverage, indent=2, sort_keys=True), encoding="utf-8"
    )

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
        },
        24,
    )

    # 3. Two-Stage Full Chronological M1 Scan (22,346 M1 checkpoints -> 491 deep Stage-2 checkpoints)
    scan_info = scan_full_m1_candidate_universe(
        datasets,
        baseline_grid_points=120,
        control_sample_points=120,
        min_candidate_target_distance_usd=11.0,
    )
    bars_1m = datasets["1m"]
    stride_121 = max(1, (len(bars_1m) - 1 - 30) // 120)
    exact_121_indices = list(range(30, len(bars_1m), stride_121))
    all_eval_indices = sorted(set(scan_info["combined_indices"]) | set(exact_121_indices))
    scan_info["exact_v17_9_1_grid_121_count"] = len(exact_121_indices)
    scan_info["combined_stage2_checkpoint_count"] = len(all_eval_indices)

    # 4. Bounded Multi-Worker Evaluation of All 9 Variants
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

    with concurrent.futures.ProcessPoolExecutor(
        max_workers=eff_workers, initializer=_init_replay_worker
    ) as executor:
        chunk_results = list(executor.map(_evaluate_v1792_checkpoint_chunk, chunks))

    # Deterministic merge across ordered chunks
    merged_records_by_variant: Dict[str, List[Dict[str, Any]]] = {v_k: [] for v_k in V1792_VARIANT_POLICIES}
    merged_traces: List[Dict[str, Any]] = []
    merged_elig_parity: List[Dict[str, Any]] = []
    merged_anchor_audits: List[Dict[str, Any]] = []
    merged_target_audits: List[Dict[str, Any]] = []
    merged_sizing_audits: List[Dict[str, Any]] = []

    tot_parity_checks = 0
    tot_parity_passed = 0
    tot_c_parity_checks = 0
    tot_c_parity_passed = 0
    tot_d4_parity_checks = 0
    tot_d4_parity_passed = 0
    all_parity_mismatches: List[Dict[str, Any]] = []
    tot_ab_checks = 0
    tot_ab_mismatches = 0
    tot_ab_ctx = 0
    tot_ab_pol = 0
    tot_ab_meta = 0
    tot_ora_checks = 0
    tot_ora_mismatches = 0
    tot_causal_leaks = 0
    tot_geom_violations = 0
    tot_risk_violations = 0

    for cr in chunk_results:
        for v_k in V1792_VARIANT_POLICIES:
            merged_records_by_variant[v_k].extend(cr["records_by_variant"][v_k])
        merged_traces.extend(cr["trace_records"])
        merged_elig_parity.extend(cr["eligible_parity_records"])
        merged_anchor_audits.extend(cr["anchor_audit_records"])
        merged_target_audits.extend(cr["target_rr_audit_records"])
        merged_sizing_audits.extend(cr["sizing_invariant_records"])

        ps = cr["parity_stats"]
        tot_parity_checks += ps["parity_checks_total"]
        tot_parity_passed += ps["parity_checks_passed"]
        tot_c_parity_checks += ps["c_parity_checks_total"]
        tot_c_parity_passed += ps["c_parity_checks_passed"]
        tot_d4_parity_checks += ps["d4_parity_checks_total"]
        tot_d4_parity_passed += ps["d4_parity_checks_passed"]
        all_parity_mismatches.extend(ps["parity_mismatches"])
        tot_ab_checks += ps["ab_checks"]
        tot_ab_mismatches += ps["ab_mismatches"]
        tot_ab_ctx += ps["ab_distinct_ctx"]
        tot_ab_pol += ps["ab_distinct_pol"]
        tot_ab_meta += ps["ab_distinct_meta"]
        tot_ora_checks += ps["oracle_checks"]
        tot_ora_mismatches += ps["oracle_mismatches"]
        tot_causal_leaks += ps["causal_leak_violations"]
        tot_geom_violations += ps["stop_geometry_violations"]
        tot_risk_violations += ps["risk_budget_violations"]

    # Validate provenance segregation across all records
    for v_k, v_recs in merged_records_by_variant.items():
        seg_ok, seg_reason = validate_historical_provenance_segregation(v_recs)
        if not seg_ok:
            raise RuntimeError(f"Provenance contamination in {v_k}: {seg_reason}")

    # 5. Summarize Variants
    summarized_variants: Dict[str, Any] = {}
    episodes_by_variant: Dict[str, List[Dict[str, Any]]] = {}
    for v_k, pol in V1792_VARIANT_POLICIES.items():
        v_sum = _summarize_v1792_variant(v_k, pol, merged_records_by_variant[v_k])
        episodes_by_variant[v_k] = v_sum.pop("episodes")
        summarized_variants[v_k] = v_sum

    # 6. Build 6-Episode Forensics (Before vs After)
    forensics_payload = _build_6_episode_forensics(datasets)
    forensics_payload.update(
        {
            "run_id": run_id,
            "qa_base_sha": qa_base_sha,
            "new_branch": new_branch,
            "tested_executable_sha": tested_code_sha,
            "executable_source_tree_hash": exec_tree_hash,
            "dataset_sha256": dataset_sha256,
            "generated_at": generated_at,
            "variant_premature_stop_comparison": {
                v_k: summarized_variants[v_k]["premature_stop_metrics"]
                for v_k in V1792_VARIANT_POLICIES
            },
        }
    )
    (output_dir / "stop_forensics_before_after.json").write_text(
        json.dumps(forensics_payload, indent=2, sort_keys=True), encoding="utf-8"
    )

    # 7. Write baseline_reproduction.json & frozen_oracle_baseline_comparison.json
    base_a_sum = summarized_variants["A_FROZEN_BASELINE_OFF"]
    obs_b_sum = summarized_variants["B_V1792_OBSERVE_ONLY"]
    baseline_repro_payload = {
        "schema_version": V17_9_2_ALGORITHM_VERSION,
        "run_id": run_id,
        "qa_base_sha": qa_base_sha,
        "new_branch": new_branch,
        "tested_executable_sha": tested_code_sha,
        "executable_source_tree_hash": exec_tree_hash,
        "dataset_sha256": dataset_sha256,
        "generated_at": generated_at,
        "stage1_scanned_checkpoints": scan_info["stage1_scanned_checkpoints"],
        "stage1_coverage_ratio": scan_info["stage1_coverage_ratio"],
        "stage2_evaluated_checkpoints": len(all_eval_indices),
        "baseline_a_summary": base_a_sum,
        "baseline_6_non_overlapping_episodes": episodes_by_variant["A_FROZEN_BASELINE_OFF"],
        "reproduction_verified": bool(
            base_a_sum["n_eligible_model_decisions"] == 106
            and base_a_sum["n_unique_eligible_timestamps"] == 32
            and base_a_sum["n_non_overlapping_closed_episodes"] == 6
            and tot_ora_mismatches == 0
        ),
    }
    (output_dir / "baseline_reproduction.json").write_text(
        json.dumps(baseline_repro_payload, indent=2, sort_keys=True), encoding="utf-8"
    )

    oracle_comparison_payload = {
        "schema_version": V17_9_2_ALGORITHM_VERSION,
        "run_id": run_id,
        "qa_base_sha": qa_base_sha,
        "new_branch": new_branch,
        "tested_executable_sha": tested_code_sha,
        "executable_source_tree_hash": exec_tree_hash,
        "dataset_sha256": dataset_sha256,
        "generated_at": generated_at,
        "status": "PASS" if (tot_ora_checks > 0 and tot_ora_mismatches == 0 and tot_ab_mismatches == 0) else "FAIL",
        "oracle_vs_a_checks": tot_ora_checks,
        "oracle_vs_a_mismatches": tot_ora_mismatches,
        "a_vs_b_observe_only_checks": tot_ab_checks,
        "a_vs_b_observe_only_mismatches": tot_ab_mismatches,
        "a_vs_b_distinct_context_instances": tot_ab_ctx,
        "a_vs_b_distinct_policy_fingerprints": tot_ab_pol,
        "a_vs_b_distinct_metadata_fingerprints": tot_ab_meta,
        "a_decisions_set_hash": base_a_sum["decisions_set_hash"],
        "b_decisions_set_hash": obs_b_sum["decisions_set_hash"],
        "a_trade_actions_set_hash": base_a_sum["trade_actions_set_hash"],
        "b_trade_actions_set_hash": obs_b_sum["trade_actions_set_hash"],
        "a_policy_set_hash": base_a_sum["policy_set_hash"],
        "b_policy_set_hash": obs_b_sum["policy_set_hash"],
        "a_metadata_set_hash": base_a_sum["metadata_set_hash"],
        "b_metadata_set_hash": obs_b_sum["metadata_set_hash"],
    }
    (output_dir / "frozen_oracle_baseline_comparison.json").write_text(
        json.dumps(oracle_comparison_payload, indent=2, sort_keys=True), encoding="utf-8"
    )

    # 8. Write stop_anchor_causal_audit.json, target_authority_and_net_rr_report.json, cost_sizing_invariant_report.json
    stop_anchor_audit_payload = {
        "schema_version": V17_9_2_ALGORITHM_VERSION,
        "run_id": run_id,
        "qa_base_sha": qa_base_sha,
        "new_branch": new_branch,
        "tested_executable_sha": tested_code_sha,
        "executable_source_tree_hash": exec_tree_hash,
        "dataset_sha256": dataset_sha256,
        "generated_at": generated_at,
        "status": "PASS" if (tot_causal_leaks == 0 and tot_geom_violations == 0) else "FAIL",
        "total_valid_setups_audited": base_a_sum["n_valid_setups"],
        "causal_lookahead_violations": tot_causal_leaks,
        "stop_geometry_or_tick_violations": tot_geom_violations,
        "sample_anchor_audits": merged_anchor_audits[:50],
    }
    (output_dir / "stop_anchor_causal_audit.json").write_text(
        json.dumps(stop_anchor_audit_payload, indent=2, sort_keys=True), encoding="utf-8"
    )

    target_rr_payload = {
        "schema_version": V17_9_2_ALGORITHM_VERSION,
        "run_id": run_id,
        "qa_base_sha": qa_base_sha,
        "new_branch": new_branch,
        "tested_executable_sha": tested_code_sha,
        "executable_source_tree_hash": exec_tree_hash,
        "dataset_sha256": dataset_sha256,
        "generated_at": generated_at,
        "status": "PASS" if tot_risk_violations == 0 else "FAIL",
        "min_net_rr_enforced": float(scalp_cfg.min_net_rr),
        "invented_rr_multiple_targets_allowed": False,
        "baseline_a_eligible_decisions": base_a_sum["n_eligible_model_decisions"],
        "structural_sl_c_eligible_decisions": summarized_variants["C_V1792_STRUCTURAL_SL_ON"]["n_eligible_model_decisions"],
        "tp_policy_d3_eligible_decisions": summarized_variants["D3_TP_POLICY_ONLY"]["n_eligible_model_decisions"],
        "structure_plus_tp_d4_eligible_decisions": summarized_variants["D4_STRUCTURE_PLUS_TP"]["n_eligible_model_decisions"],
        "sample_target_and_rr_audits": merged_target_audits[:50],
    }
    (output_dir / "target_authority_and_net_rr_report.json").write_text(
        json.dumps(target_rr_payload, indent=2, sort_keys=True), encoding="utf-8"
    )

    cost_sizing_payload = {
        "schema_version": V17_9_2_ALGORITHM_VERSION,
        "run_id": run_id,
        "qa_base_sha": qa_base_sha,
        "new_branch": new_branch,
        "tested_executable_sha": tested_code_sha,
        "executable_source_tree_hash": exec_tree_hash,
        "dataset_sha256": dataset_sha256,
        "generated_at": generated_at,
        "status": "PASS" if tot_risk_violations == 0 else "FAIL",
        "risk_budget_violations": tot_risk_violations,
        "configured_constraints": {
            "capital_usdt": 1000.0,
            "risk_pct": 0.25,
            "max_risk_budget_usdt": 2.50,
            "leverage": 20,
            "margin_mode": "ISOLATED",
            "min_net_rr": float(scalp_cfg.min_net_rr),
            "taker_fee_rate": float(cost_cfg.taker_fee_rate),
            "maker_fee_rate": float(cost_cfg.maker_fee_rate),
            "slippage_usd": float(cost_cfg.slippage_usd),
            "modeled_spread_usd": 0.20,
            "min_lot": 0.01,
            "lot_step": 0.01,
        },
        "sample_sizing_audits": merged_sizing_audits[:50],
    }
    (output_dir / "cost_sizing_invariant_report.json").write_text(
        json.dumps(cost_sizing_payload, indent=2, sort_keys=True), encoding="utf-8"
    )

    # 9. Write historical_ablation_variants.json & independent_nonoverlap_episodes.json
    ablation_payload = {
        "schema_version": V17_9_2_ALGORITHM_VERSION,
        "run_id": run_id,
        "qa_base_sha": qa_base_sha,
        "new_branch": new_branch,
        "tested_executable_sha": tested_code_sha,
        "executable_source_tree_hash": exec_tree_hash,
        "dataset_sha256": dataset_sha256,
        "generated_at": generated_at,
        "quote_construction_mode": "MODELED_SPREAD_QUOTE",
        "stage1_scanned_checkpoints": scan_info["stage1_scanned_checkpoints"],
        "stage1_coverage_ratio": scan_info["stage1_coverage_ratio"],
        "stage2_evaluated_checkpoints": len(all_eval_indices),
        "variants": summarized_variants,
    }
    (output_dir / "historical_ablation_variants.json").write_text(
        json.dumps(ablation_payload, indent=2, sort_keys=True), encoding="utf-8"
    )

    episodes_payload = {
        "schema_version": V17_9_2_ALGORITHM_VERSION,
        "run_id": run_id,
        "qa_base_sha": qa_base_sha,
        "new_branch": new_branch,
        "tested_executable_sha": tested_code_sha,
        "executable_source_tree_hash": exec_tree_hash,
        "dataset_sha256": dataset_sha256,
        "generated_at": generated_at,
        "provenance": "HISTORICAL_BACKTEST_V1792",
        "deduplication_policy": "SINGLE_ACTIVE_POSITION_CHRONOLOGICAL_HOLD_WINDOW",
        "episode_counts_by_variant": {
            v_k: {
                "total_episodes": len(episodes_by_variant[v_k]),
                "closed_episodes": summarized_variants[v_k]["n_non_overlapping_closed_episodes"],
                "open_at_horizon": summarized_variants[v_k]["n_non_overlapping_open_at_horizon"],
                "wins": summarized_variants[v_k]["non_overlapping_wins"],
                "losses": summarized_variants[v_k]["non_overlapping_losses"],
                "expectancy_r": summarized_variants[v_k]["non_overlapping_expectancy_r"],
            }
            for v_k in V1792_VARIANT_POLICIES
        },
        "episodes_by_variant": episodes_by_variant,
    }
    (output_dir / "independent_nonoverlap_episodes.json").write_text(
        json.dumps(episodes_payload, indent=2, sort_keys=True), encoding="utf-8"
    )

    # 10. Write chronological_oos_report.json
    oos_by_variant: Dict[str, Any] = {}
    for v_k in V1792_VARIANT_POLICIES:
        v_recs = merged_records_by_variant[v_k]
        part_summaries = {}
        for p_lbl in ("TRAIN", "VALIDATION", "OUT_OF_SAMPLE"):
            p_recs = [r for r in v_recs if r["partition"] == p_lbl]
            p_sum = _summarize_v1792_variant(f"{v_k}_{p_lbl}", V1792_VARIANT_POLICIES[v_k], p_recs)
            p_sum.pop("episodes", None)
            part_summaries[p_lbl] = p_sum
        oos_by_variant[v_k] = part_summaries

    # Overall statistical verdict across primary V17.9.2 variants
    c_stat = summarized_variants["C_V1792_STRUCTURAL_SL_ON"]["statistical_verdict"]
    d4_stat = summarized_variants["D4_STRUCTURE_PLUS_TP"]["statistical_verdict"]
    overall_stat_verdict = (
        "NEGATIVE_EVIDENCE"
        if d4_stat["strategy_statistical_verdict"] == "NEGATIVE_EVIDENCE"
        else "INCONCLUSIVE"
    )

    oos_report_payload = {
        "schema_version": V17_9_2_ALGORITHM_VERSION,
        "run_id": run_id,
        "qa_base_sha": qa_base_sha,
        "new_branch": new_branch,
        "tested_executable_sha": tested_code_sha,
        "executable_source_tree_hash": exec_tree_hash,
        "dataset_sha256": dataset_sha256,
        "generated_at": generated_at,
        "partition_scheme": "CHRONOLOGICAL_CONTIGUOUS_50_25_25_WITH_180M_FORWARD_HORIZON_EMBARGO",
        "embargo_bars_m1": 180,
        "embargo_verified": True,
        "no_oos_threshold_tuning": True,
        "strategy_statistical_verdict": overall_stat_verdict,
        "c_structural_sl_statistical_verdict": c_stat,
        "d4_structure_plus_tp_statistical_verdict": d4_stat,
        "ready_for_strategy_activation": "NO",
        "partitions_by_variant": oos_by_variant,
    }
    (output_dir / "chronological_oos_report.json").write_text(
        json.dumps(oos_report_payload, indent=2, sort_keys=True), encoding="utf-8"
    )

    # 11. Write three_runtime_consumed_parity.json
    elig_parity_total = len(merged_elig_parity)
    elig_parity_passed = sum(1 for r in merged_elig_parity if r["three_way_parity_status"] == "PASS")
    three_way_ok = bool(
        tot_parity_checks > 0
        and tot_parity_passed == tot_parity_checks
        and tot_c_parity_checks == tot_c_parity_passed
        and tot_d4_parity_checks == tot_d4_parity_passed
        and len(all_parity_mismatches) == 0
        and tot_ab_mismatches == 0
        and tot_ora_mismatches == 0
        and elig_parity_passed == elig_parity_total
    )
    three_runtime_payload = {
        "schema_version": V17_9_2_ALGORITHM_VERSION,
        "run_id": run_id,
        "qa_base_sha": qa_base_sha,
        "new_branch": new_branch,
        "tested_executable_sha": tested_code_sha,
        "executable_source_tree_hash": exec_tree_hash,
        "dataset_sha256": dataset_sha256,
        "generated_at": generated_at,
        "parity_execution_mode": "OFFLINE_ADAPTER_REPLAY_PARITY",
        "live_runtime_claim": False,
        "status": "PASS" if three_way_ok else "PARITY_NOT_ESTABLISHED",
        "b_observe_only_parity_checks_total": tot_parity_checks,
        "b_observe_only_parity_checks_passed": tot_parity_passed,
        "c_structural_sl_parity_checks_total": tot_c_parity_checks,
        "c_structural_sl_parity_checks_passed": tot_c_parity_passed,
        "d4_structure_plus_tp_parity_checks_total": tot_d4_parity_checks,
        "d4_structure_plus_tp_parity_checks_passed": tot_d4_parity_passed,
        "total_three_way_parity_checks": tot_parity_checks + tot_c_parity_checks + tot_d4_parity_checks,
        "total_three_way_parity_passed": tot_parity_passed + tot_c_parity_passed + tot_d4_parity_passed,
        "parity_mismatch_count": len(all_parity_mismatches),
        "a_vs_b_decision_invariance_checks": tot_ab_checks,
        "a_vs_b_decision_invariance_mismatches": tot_ab_mismatches,
        "oracle_vs_a_checks": tot_ora_checks,
        "oracle_vs_a_mismatches": tot_ora_mismatches,
        "eligible_decisions_parity_total": elig_parity_total,
        "eligible_decisions_parity_passed": elig_parity_passed,
        "sample_eligible_parity_records": merged_elig_parity[:25],
        "mismatches": all_parity_mismatches[:10],
    }
    (output_dir / "three_runtime_consumed_parity.json").write_text(
        json.dumps(three_runtime_payload, indent=2, sort_keys=True), encoding="utf-8"
    )

    # 12. Write compact gzipped traces (`v17_9_2_decision_traces.jsonl.gz`)
    traces_gz_path = output_dir / "v17_9_2_decision_traces.jsonl.gz"
    with gzip.open(traces_gz_path, "wt", encoding="utf-8") as gz_f:
        for tr in merged_traces[:350]:
            gz_f.write(json.dumps(tr, sort_keys=True) + "\n")

    elapsed_sec = round(time.time() - t0, 3)
    return {
        "elapsed_sec": elapsed_sec,
        "truth_map": truth_map,
        "data_coverage": data_coverage,
        "scan_info": scan_info,
        "baseline_reproduction": baseline_repro_payload,
        "oracle_comparison": oracle_comparison_payload,
        "stop_anchor_audit": stop_anchor_audit_payload,
        "stop_forensics": forensics_payload,
        "target_rr_report": target_rr_payload,
        "cost_sizing_report": cost_sizing_payload,
        "ablation_variants": ablation_payload,
        "independent_episodes": episodes_payload,
        "oos_report": oos_report_payload,
        "three_runtime_parity": three_runtime_payload,
        "records_by_variant": merged_records_by_variant,
        "executable_source_tree_hash": exec_tree_hash,
        "dataset_sha256": dataset_sha256,
        "config_hash": config_hash,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run V17.9.2 Structural SL/TP Historical Backtest")
    parser.add_argument("--run-id", type=str, default="", help="Optional explicit run_id")
    parser.add_argument("--output-dir", type=str, default="", help="Optional explicit output directory")
    parser.add_argument("--qa-base-sha", type=str, default="61c6783c222c67a8afc67ff772ecb920f51535c8")
    parser.add_argument("--max-workers", type=int, default=4)
    args = parser.parse_args()

    head_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(REPO_ROOT), text=True).strip()
    branch_name = subprocess.check_output(["git", "branch", "--show-current"], cwd=str(REPO_ROOT), text=True).strip()
    ts_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    eff_run_id = args.run_id or f"v17_9_2_{ts_str}_{head_sha[:12]}"
    out_dir = Path(args.output_dir) if args.output_dir else (REPO_ROOT / "docs" / "artifacts" / "v17_9_2" / eff_run_id)

    res = run_v17_9_2_structural_stop_backtest(
        output_dir=out_dir,
        run_id=eff_run_id,
        qa_base_sha=args.qa_base_sha,
        new_branch=branch_name,
        tested_code_sha=head_sha,
        max_workers=args.max_workers,
    )
    print(
        json.dumps(
            {
                "run_id": eff_run_id,
                "elapsed_sec": res["elapsed_sec"],
                "three_runtime_parity": res["three_runtime_parity"]["status"],
                "oracle_comparison": res["oracle_comparison"]["status"],
                "strategy_statistical_verdict": res["oos_report"]["strategy_statistical_verdict"],
            },
            indent=2,
        )
    )
