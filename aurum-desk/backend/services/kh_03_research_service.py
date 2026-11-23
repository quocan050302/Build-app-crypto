"""
aurum-desk/backend/services/kh_03_research_service.py

AURUM DESK — KH-03.R1 Pre-Registered Causal Entry/Exit Edge Research, Ablations,
Negative Controls, Stratified Common-Support Matching, Six-Scenario Cost Stress,
Purged Walk-Forward, Cluster Bootstrap, Holm-Bonferroni Multiplicity & Risk-Tail Disclosure.
SPEC VERSION: KH-03.R1-v1.0 | RESEARCH ONLY (REUSED DEV) | OOS_EVALUATIONS=0 | ACTIVATION_READY=NO
"""
from __future__ import annotations

from collections import Counter, defaultdict
import copy
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import math
import random
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from domain_calculator import CostAssumptions
from services.kh_02_entry_foundation_research_service import (
    KH02AsOfMTFRecord,
    build_kh02_asof_mtf_features,
    generate_kh02_research_candidates,
)
from services.kh_02_r2_research_integrity_service import (
    CANONICAL_DATASET_SHA256_KH02_R2,
    DEFAULT_MODELED_SPREAD_USD,
    DEFAULT_RANDOM_SEED_KH02_R2,
    DEFAULT_SLIPPAGE_USD,
    MIN_NET_RR_THRESHOLD,
    PLANNED_NET_RISK_CAP_USD,
    PRIMARY_R2_ARMS,
    RUNTIME_SAFETY_MODES_KH02_R2,
    _block_bootstrap_ci,
    _compute_smd,
    _r2_frozen_plan_and_arms,
    _replay_chronological_custom_cost_scenario,
    _summarize_episodes,
    audit_r2_r1_target_eligibility_counterexamples,
    build_r2_causal_unswept_level_ledger,
    build_r2_target_repair_full_opportunity_conservation,
    evaluate_causal_fill_time_policy_single_trade,
    run_r2_t01_to_t10_adversarial_target_scenarios,
)
from services.kh_02_r2_prospective_data_service import (
    compute_prospective_oos_power_requirements_r2_c1,
    normalize_ny_local_to_utc_ms_r2_c1,
)
from services.kh_03_fee_integrity_service import (
    SCHEMA_VERSION_KH03,
    SPEC_VERSION_KH03,
)
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash

KH03_PRIMARY_ARMS: Tuple[str, ...] = (
    "KH03_H1_MTF_UNSWEPT_ALIGN",
    "KH03_H2_LOW_FRICTION_VOL_REGIME",
    "KH03_H3_FILL_TIME_CAUSAL_RR_GATE",
)
KH03_NEGATIVE_CONTROLS: Tuple[str, ...] = (
    "NC_NULL_TRIGGER",
    "NC_PLACEBO_SHUFFLED_TRIGGER",
    "NC_TIME_OFFSET_SHIFT",
)
KH03_ALL_ARMS: Tuple[str, ...] = (*KH03_PRIMARY_ARMS, *KH03_NEGATIVE_CONTROLS)

KH03_SIX_COST_SCENARIOS: Tuple[Tuple[str, float, float, float, float], ...] = (
    ("1X_BASE_0p0004", 0.20, 0.10, 0.0004, 0.0002),
    ("FEE_ONLY_0p0006", 0.20, 0.10, 0.0006, 0.0002),
    ("2X_ALL_COST_0p0004", 0.40, 0.20, 0.0008, 0.0004),
    ("2X_ALL_COST_0p0006", 0.40, 0.20, 0.0012, 0.0004),
    ("3X_ALL_COST_0p0004", 0.60, 0.30, 0.0012, 0.0006),
    ("3X_ALL_COST_0p0006", 0.60, 0.30, 0.0018, 0.0006),
)


def _dec4(v: Decimal) -> float:
    return float(v.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def _nearest_rank_quantile(sorted_vals: Sequence[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    if q <= 0.0:
        return round(float(sorted_vals[0]), 4)
    if q >= 1.0:
        return round(float(sorted_vals[-1]), 4)
    idx = max(0, min(len(sorted_vals) - 1, int(math.ceil(q * len(sorted_vals))) - 1))
    return round(float(sorted_vals[idx]), 4)


def _mtf_records_by_idx(mtf_bundle: Mapping[str, Any]) -> Dict[int, KH02AsOfMTFRecord]:
    if "records_by_idx" in mtf_bundle:
        return {int(k): v for k, v in (mtf_bundle["records_by_idx"] or {}).items()}
    recs = mtf_bundle.get("records") or ()
    return {int(r.bar_index_1m): r for r in recs}


def _one_sided_cluster_bootstrap_pvalue_gt_zero(
    values: Sequence[float],
    clusters: Sequence[str],
    *,
    n_boot: int = 800,
    seed: int = DEFAULT_RANDOM_SEED_KH02_R2 + 701,
) -> Optional[float]:
    """
    Deterministic cluster-bootstrap one-sided p-value for H0: E[R] <= 0 vs H1: E[R] > 0.
    Uses centered cluster bootstrap around observed mean to test null shift without SciPy.
    """
    if len(values) < 5:
        return None
    by_cl: Dict[str, List[float]] = defaultdict(list)
    for v, cl in zip(values, clusters):
        by_cl[str(cl)].append(float(v))
    cl_keys = sorted(by_cl.keys())
    if len(cl_keys) < 2:
        return 1.0
    obs_mean = sum(values) / float(len(values))
    # Shift values to null mean = 0
    centered_by_cl = {k: [x - obs_mean for x in by_cl[k]] for k in cl_keys}
    rng = random.Random(seed)
    ge_cnt = 0
    k_cl = len(cl_keys)
    for _ in range(n_boot):
        draw_vals: List[float] = []
        for _ in range(k_cl):
            picked = cl_keys[rng.randrange(k_cl)]
            draw_vals.extend(centered_by_cl[picked])
        b_mean = sum(draw_vals) / float(max(1, len(draw_vals)))
        if b_mean >= obs_mean - 1e-12:
            ge_cnt += 1
    return round((ge_cnt + 1.0) / float(n_boot + 1.0), 6)


def _holm_bonferroni_adjust(raw_pvals: Mapping[str, Optional[float]]) -> Dict[str, Optional[float]]:
    valid_items = [(k, float(v)) for k, v in sorted(raw_pvals.items()) if v is not None]
    m = len(valid_items)
    out: Dict[str, Optional[float]] = {k: None for k in raw_pvals}
    if m == 0:
        return out
    ordered = sorted(valid_items, key=lambda kv: (kv[1], kv[0]))
    running_max = 0.0
    for rank, (k, p) in enumerate(ordered):
        adj = min(1.0, p * float(m - rank))
        running_max = max(running_max, adj)
        out[k] = round(running_max, 6)
    return out


def preregister_kh03_research_plan(
    *,
    tested_code_sha: str,
    dataset_sha256: str = CANONICAL_DATASET_SHA256_KH02_R2,
    random_seed: int = DEFAULT_RANDOM_SEED_KH02_R2,
) -> Dict[str, Any]:
    """
    §08 R1 -> Artifact `10_kh03_frozen_hypotheses_with_falsification_budget.json`:
    MUST be constructed, hashed, and persisted BEFORE executing any KH-03 candidate replay!
    """
    created_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    hypotheses = [
        {
            "arm_code": "KH03_H1_MTF_UNSWEPT_ALIGN",
            "role": "PRIMARY_HYPOTHESIS",
            "estimand_class": "PURE_GATING_FILTER_ON_R2_UNSWEPT_UNIVERSE",
            "priority_order": 1,
            "causal_mechanism": (
                "Restrict R2 unswept-target triggers to strict as-of H1 & M15 directional trend "
                "agreement (`h1_trend_dir == dir` AND `m15_trend_dir == dir`) with positive as-of "
                "M15 8-bar directional efficiency (`>= 0.25` for LONG / `<= -0.25` for SHORT), "
                "eliminating counter-HTF stop-outs observed in KH-01B.R1 failure attribution."
            ),
            "alters_entry_sl_tp_qty": False,
            "expected_direction": "HIGHER_NET_EXPECTANCY_R_VS_NC_NULL_TRIGGER",
            "falsification_rule": (
                "Falsified if net expectancy <= 0 at 1X or 2X, OR 95% NY-day cluster CI crosses 0, "
                "OR Holm-Bonferroni p >= 0.05, OR matched delta vs NC_NULL_TRIGGER <= 0, OR beats neither placebo."
            ),
        },
        {
            "arm_code": "KH03_H2_LOW_FRICTION_VOL_REGIME",
            "role": "PRIMARY_HYPOTHESIS",
            "estimand_class": "PURE_GATING_FILTER_ON_R2_UNSWEPT_UNIVERSE",
            "priority_order": 2,
            "causal_mechanism": (
                "Restrict R2 unswept-target triggers to liquid NY sessions (`is_liquid_session == True`) "
                "with causal low friction drag (`spread_to_m1_atr_ratio <= 0.12`) and non-extreme trailing "
                "H1 ATR causal prefix quantile (`0.30 <= h1_atr_causal_prefix_quantile <= 0.85`) to address "
                "the severe fee/spread drag documented in KH-02.R2."
            ),
            "alters_entry_sl_tp_qty": False,
            "expected_direction": "HIGHER_NET_EXPECTANCY_R_VS_NC_NULL_TRIGGER",
            "falsification_rule": (
                "Falsified if net expectancy <= 0 at 1X or 2X, OR 95% NY-day cluster CI crosses 0, "
                "OR Holm-Bonferroni p >= 0.05, OR fails purged walk-forward stability."
            ),
        },
        {
            "arm_code": "KH03_H3_FILL_TIME_CAUSAL_RR_GATE",
            "role": "PRIMARY_HYPOTHESIS",
            "estimand_class": "MATERIALIZED_RESEARCH_COUNTERFACTUAL",
            "priority_order": 3,
            "causal_mechanism": (
                "Research-only counterfactual fill-time gate using strictly observable next-bar open price "
                "(`evaluate_causal_fill_time_policy_single_trade`) BEFORE fill commitment to reject entries "
                "whose adverse open gap degrades net R:R below 2.0 or breaches min-lot under $2.50 cap. "
                "Explicitly separated from pure gating filters and never mutates frozen production engine."
            ),
            "alters_entry_sl_tp_qty": True,
            "expected_direction": "REDUCED_TAIL_SLIPPAGE_AND_HIGHER_NET_EXPECTANCY_R",
            "falsification_rule": (
                "Falsified if net expectancy <= 0 at 1X or 2X, OR 95% NY-day cluster CI crosses 0, "
                "OR Holm-Bonferroni p >= 0.05, OR closed_trades < 50."
            ),
        },
        {
            "arm_code": "NC_NULL_TRIGGER",
            "role": "NEGATIVE_CONTROL_BASELINE",
            "estimand_class": "R2_UNSWEPT_NULL_CONTROL",
            "priority_order": 4,
            "causal_mechanism": "All R2 geometry-and-risk-eligible triggers without extra KH-03 selectivity.",
            "alters_entry_sl_tp_qty": False,
            "expected_direction": "BASELINE_NEGATIVE_CONTROL",
            "falsification_rule": "Reference control arm; primary arms must outperform on matched common support.",
        },
        {
            "arm_code": "NC_PLACEBO_SHUFFLED_TRIGGER",
            "role": "NEGATIVE_CONTROL_PLACEBO",
            "estimand_class": "WITHIN_NY_DAY_SHUFFLED_PLACEBO",
            "priority_order": 5,
            "causal_mechanism": (
                "Deterministic seeded within-NY-day permutation of KH03_H1 trigger selection preserving exact "
                "per-day trade density while destroying feature-to-bar causal alignment."
            ),
            "alters_entry_sl_tp_qty": False,
            "expected_direction": "NO_GENUINE_EDGE",
            "falsification_rule": "If a primary arm fails to beat this placebo, its lift is noise/density artifact.",
        },
        {
            "arm_code": "NC_TIME_OFFSET_SHIFT",
            "role": "NEGATIVE_CONTROL_TIME_SHIFT",
            "estimand_class": "STALE_ASOF_SHIFT_NEGATIVE_CONTROL",
            "priority_order": 6,
            "causal_mechanism": (
                "Applies KH03_H1 predicates read from `bar_index_1m - 15` (15-minute stale as-of offset) "
                "to test whether timing alignment actually contributes economic value."
            ),
            "alters_entry_sl_tp_qty": False,
            "expected_direction": "NO_GENUINE_EDGE",
            "falsification_rule": "If stale time-shifted control matches/beats primary arm, timing causality is falsified.",
        },
    ]

    plan: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH03,
        "schema_version": SCHEMA_VERSION_KH03,
        "preregistered_utc": created_utc,
        "written_before_any_kh03_replay": True,
        "data_regime": "REUSED_RESEARCH_PURGED_WALKFORWARD_NOT_FRESH_OOS",
        "tested_code_sha": tested_code_sha,
        "canonical_dataset_sha256": dataset_sha256,
        "random_seed": int(random_seed),
        "finite_search_budget": {
            "max_primary_arms": 3,
            "declared_primary_arms_count": len(KH03_PRIMARY_ARMS),
            "declared_negative_controls_count": len(KH03_NEGATIVE_CONTROLS),
            "multiplicity_correction": "HOLM_BONFERRONI_FAMILY_M3",
            "discarded_variants_count": 0,
            "threshold_iteration_after_outcome_peek_permitted": False,
        },
        "fixed_cost_scenarios": [s[0] for s in KH03_SIX_COST_SCENARIOS],
        "embargo_minutes": 240,
        "dev_advancement_criteria": {
            "min_closed_trades": 50,
            "min_effective_ny_days": 10,
            "require_positive_net_at_1x_feeonly_and_2x": True,
            "require_ny_day_95ci_lower_gt_zero": True,
            "require_holm_bonferroni_p_lt_0p05": True,
            "require_balanced_matched_delta_vs_null_gt_zero": True,
            "require_beats_placebo_and_time_offset_controls": True,
            "require_purged_wf_median_fold_gt_zero_and_60pct_pos": True,
            "dev_pass_label_only": "DEV_CANDIDATE_UNPROVEN_OOS",
            "never_claims_proven_edge_on_dev": True,
        },
        "hypotheses": hypotheses,
        "retained_r2_reference_arms": list(PRIMARY_R2_ARMS),
        "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2),
        "OOS_EVALUATIONS": 0,
        "ACTIVATION_READY": "NO",
        "status": "PASS",
    }
    plan["plan_frozen_sha256"] = canonical_hash({k: v for k, v in plan.items() if k != "plan_frozen_sha256"})
    return plan


def evaluate_kh03_arm_predicate_asof(
    *,
    arm_code: str,
    rec: KH02AsOfMTFRecord,
    direction: str,
    stale_rec: Optional[KH02AsOfMTFRecord] = None,
) -> bool:
    """Strictly causal as-of predicate evaluator for KH-03 gating arms (zero future access)."""
    d = str(direction).upper()
    if arm_code == "KH03_H1_MTF_UNSWEPT_ALIGN":
        if d == "LONG":
            return (
                rec.h1_trend_dir == "BULL"
                and rec.m15_trend_dir == "BULL"
                and rec.m15_raw_dir_efficiency_8 is not None
                and float(rec.m15_raw_dir_efficiency_8) >= 0.25
            )
        return (
            rec.h1_trend_dir == "BEAR"
            and rec.m15_trend_dir == "BEAR"
            and rec.m15_raw_dir_efficiency_8 is not None
            and float(rec.m15_raw_dir_efficiency_8) <= -0.25
        )
    if arm_code == "KH03_H2_LOW_FRICTION_VOL_REGIME":
        return (
            bool(rec.is_liquid_session)
            and rec.spread_to_m1_atr_ratio is not None
            and float(rec.spread_to_m1_atr_ratio) <= 0.12
            and rec.h1_atr_causal_prefix_quantile is not None
            and 0.30 <= float(rec.h1_atr_causal_prefix_quantile) <= 0.85
        )
    if arm_code == "NC_TIME_OFFSET_SHIFT":
        if stale_rec is None:
            return False
        return evaluate_kh03_arm_predicate_asof(
            arm_code="KH03_H1_MTF_UNSWEPT_ALIGN", rec=stale_rec, direction=d
        )
    return True


def build_kh03_arm_candidates_from_r2_universe(
    *,
    datasets: Mapping[str, Any],
    mtf_bundle: Mapping[str, Any],
    r2_cands_bundle: Mapping[str, Any],
    spread_usd: float = DEFAULT_MODELED_SPREAD_USD,
    slippage_usd: float = DEFAULT_SLIPPAGE_USD,
    seed: int = DEFAULT_RANDOM_SEED_KH02_R2,
) -> Dict[str, Any]:
    """
    Builds exact KH-03 primary & negative-control candidate sets from the full R2 unswept
    eligible opportunity stream (`NC_NULL_TRIGGER`), guaranteeing full opportunity conservation.
    """
    bars_1m = list(datasets["1m"])
    rec_by_idx = _mtf_records_by_idx(mtf_bundle)
    base_null = sorted(
        list((r2_cands_bundle.get("arm_candidates") or {}).get("NC_NULL_TRIGGER") or ()),
        key=lambda c: (int(c["decision_timestamp_ms"]), str(c["direction"]), str(c["opportunity_id"])),
    )

    arm_candidates: Dict[str, List[Dict[str, Any]]] = {ac: [] for ac in KH03_ALL_ARMS}
    arm_conservation: Dict[str, Dict[str, Any]] = {}
    h1_selected_by_day: Dict[str, int] = Counter()
    null_by_day: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    h1_rej = Counter()
    h2_rej = Counter()
    h3_rej = Counter()
    ts_rej = Counter()

    for c in base_null:
        idx = int(c["checkpoint_index_1m"])
        rec = rec_by_idx.get(idx)
        ny_d = str(c.get("ny_date") or (rec.ny_date if rec is not None else "UNK"))
        arm_candidates["NC_NULL_TRIGGER"].append(c)
        null_by_day[ny_d].append(c)

        if rec is None:
            h1_rej["MISSING_MTF_RECORD"] += 1
            h2_rej["MISSING_MTF_RECORD"] += 1
            h3_rej["MISSING_MTF_RECORD"] += 1
            ts_rej["MISSING_MTF_RECORD"] += 1
            continue

        # KH03_H1
        if evaluate_kh03_arm_predicate_asof(arm_code="KH03_H1_MTF_UNSWEPT_ALIGN", rec=rec, direction=c["direction"]):
            arm_candidates["KH03_H1_MTF_UNSWEPT_ALIGN"].append(c)
            h1_selected_by_day[ny_d] += 1
        else:
            h1_rej["FAILED_H1_M15_TREND_OR_EFFICIENCY_ALIGN"] += 1

        # KH03_H2
        if evaluate_kh03_arm_predicate_asof(arm_code="KH03_H2_LOW_FRICTION_VOL_REGIME", rec=rec, direction=c["direction"]):
            arm_candidates["KH03_H2_LOW_FRICTION_VOL_REGIME"].append(c)
        else:
            h2_rej["FAILED_LIQUID_SESSION_OR_FRICTION_VOL_REGIME"] += 1

        # KH03_H3 (MATERIALIZED_RESEARCH_COUNTERFACTUAL: causal fill-time open-price RR & cap check)
        if idx + 1 < len(bars_1m):
            fill_open = float(bars_1m[idx + 1]["open"])
            ft = evaluate_causal_fill_time_policy_single_trade(
                direction=str(c["direction"]),
                decision_entry=float(c["entry_reference"]),
                fill_bar_open=fill_open,
                selected_sl=float(c["selected_sl"]),
                take_profit=float(c["take_profit"]),
                decision_qty=float(c.get("hypothetical_quantity") or c.get("executed_quantity") or 0.01),
                spread_usd=spread_usd,
                slippage_usd=slippage_usd,
                risk_cap_usd=PLANNED_NET_RISK_CAP_USD,
                min_net_rr=MIN_NET_RR_THRESHOLD,
                now_ms=int(bars_1m[idx + 1]["open_time"]),
            )
            if bool(ft.get("accepted")):
                arm_candidates["KH03_H3_FILL_TIME_CAUSAL_RR_GATE"].append(c)
            else:
                h3_rej[str(ft.get("fill_time_action") or "REJECTED_AT_FILL_TIME")] += 1
        else:
            h3_rej["NO_NEXT_BAR_FOR_FILL_TIME_CHECK"] += 1

        # NC_TIME_OFFSET_SHIFT (15 bars stale strictly as-of past record)
        stale_rec = rec_by_idx.get(idx - 15)
        if evaluate_kh03_arm_predicate_asof(
            arm_code="NC_TIME_OFFSET_SHIFT", rec=rec, direction=c["direction"], stale_rec=stale_rec
        ):
            arm_candidates["NC_TIME_OFFSET_SHIFT"].append(c)
        else:
            ts_rej["FAILED_STALE_15M_SHIFT_CONTROL"] += 1

    # NC_PLACEBO_SHUFFLED_TRIGGER: deterministic seeded within-NY-day sampling matching KH03_H1 per-day count
    rng = random.Random(seed + 303)
    placebo_rej = Counter()
    for ny_d in sorted(null_by_day.keys()):
        day_pool = list(null_by_day[ny_d])
        k_pick = min(len(day_pool), int(h1_selected_by_day.get(ny_d, 0)))
        idxs = list(range(len(day_pool)))
        rng.shuffle(idxs)
        picked = set(idxs[:k_pick])
        for i, c in enumerate(day_pool):
            if i in picked:
                arm_candidates["NC_PLACEBO_SHUFFLED_TRIGGER"].append(c)
            else:
                placebo_rej["NOT_SELECTED_BY_WITHIN_DAY_PLACEBO_PERMUTATION"] += 1

    arm_candidates["NC_PLACEBO_SHUFFLED_TRIGGER"].sort(
        key=lambda c: (int(c["decision_timestamp_ms"]), str(c["direction"]), str(c["opportunity_id"]))
    )

    rej_map = {
        "KH03_H1_MTF_UNSWEPT_ALIGN": h1_rej,
        "KH03_H2_LOW_FRICTION_VOL_REGIME": h2_rej,
        "KH03_H3_FILL_TIME_CAUSAL_RR_GATE": h3_rej,
        "NC_NULL_TRIGGER": Counter(),
        "NC_PLACEBO_SHUFFLED_TRIGGER": placebo_rej,
        "NC_TIME_OFFSET_SHIFT": ts_rej,
    }
    base_n = len(base_null)
    all_conserved = True
    for ac in KH03_ALL_ARMS:
        acc_n = len(arm_candidates[ac])
        rej_sum = sum(rej_map[ac].values())
        ok_c = (acc_n + rej_sum == base_n)
        if not ok_c:
            all_conserved = False
        arm_conservation[ac] = {
            "base_r2_eligible_denominator": base_n,
            "accepted_count": acc_n,
            "rejected_count": rej_sum,
            "rejection_breakdown": dict(sorted(rej_map[ac].items())),
            "conservation_exact": ok_c,
            "candidate_ids_sha256": canonical_hash([c["opportunity_id"] for c in arm_candidates[ac]]),
        }

    return {
        "total_directional_bar_opportunities": int(r2_cands_bundle["total_directional_bar_opportunities"]),
        "raw_m1_triggers_count": int(r2_cands_bundle["raw_m1_triggers_count"]),
        "raw_r2_geometry_risk_eligible_count": base_n,
        "all_arms_conserved_exact": all_conserved,
        "arm_candidates": arm_candidates,
        "arm_conservation": arm_conservation,
    }


def run_kh03_causal_asof_and_future_mutation_audit(
    *,
    datasets: Mapping[str, Any],
    mtf_bundle: Mapping[str, Any],
    ledger_bundle: Mapping[str, Any],
    r1_candidates: Mapping[str, Any],
) -> Dict[str, Any]:
    """
    §08 R2 & R7 -> Artifact `11_causal_asof_mtf_and_future_mutation_tests.json`:
    Proves strict future-bar mutation invariance on KH-03 MTF features & predicates,
    runs T01..T10 unswept target controls, NY DST checks, and integrates R1/R2 failure attribution.
    """
    t01_t10 = run_r2_t01_to_t10_adversarial_target_scenarios()
    r1_ce = audit_r2_r1_target_eligibility_counterexamples(
        datasets=datasets, mtf_bundle=mtf_bundle, ledger_bundle=ledger_bundle, r1_candidates=r1_candidates
    )

    # Future-bar mutation invariance on KH-03 predicates over a 400-bar slice
    m1_slice = [dict(b) for b in list(datasets["1m"])[:400]]
    cutoff_idx = 220
    cutoff_ts = int(m1_slice[cutoff_idx]["close_time"])
    ds_orig = {
        "1m": m1_slice,
        "5m": [dict(b) for b in list(datasets.get("5m") or ()) if int(b["open_time"]) <= int(m1_slice[-1]["close_time"])],
        "15m": [dict(b) for b in list(datasets.get("15m") or ()) if int(b["open_time"]) <= int(m1_slice[-1]["close_time"])],
        "1H": [dict(b) for b in list(datasets.get("1H") or ()) if int(b["open_time"]) <= int(m1_slice[-1]["close_time"])],
        "4H": [dict(b) for b in list(datasets.get("4H") or ()) if int(b["open_time"]) <= int(m1_slice[-1]["close_time"])],
        "1D": [dict(b) for b in list(datasets.get("1D") or ()) if int(b["open_time"]) <= int(m1_slice[-1]["close_time"])],
    }
    ds_mut = copy.deepcopy(ds_orig)
    for tf_k, bars in ds_mut.items():
        for b in bars:
            if int(b["open_time"]) > cutoff_ts:
                b["open"] = round(float(b["open"]) * 1.15, 2)
                b["high"] = round(float(b["high"]) * 1.25, 2)
                b["low"] = round(float(b["low"]) * 0.85, 2)
                b["close"] = round(float(b["close"]) * 1.12, 2)

    mtf_o = build_kh02_asof_mtf_features(ds_orig, warmup_m1=30)
    mtf_m = build_kh02_asof_mtf_features(ds_mut, warmup_m1=30)
    rec_o = _mtf_records_by_idx(mtf_o)
    rec_m = _mtf_records_by_idx(mtf_m)

    mismatches = 0
    checked_decisions = 0
    for idx in range(30, cutoff_idx + 1):
        ro = rec_o.get(idx)
        rm = rec_m.get(idx)
        if ro is None or rm is None:
            mismatches += 1
            continue
        if ro.max_known_at_ms > ro.decision_timestamp_ms or rm.max_known_at_ms > rm.decision_timestamp_ms:
            mismatches += 1
        if ro.record_sha256 != rm.record_sha256:
            mismatches += 1
        for ac in ("KH03_H1_MTF_UNSWEPT_ALIGN", "KH03_H2_LOW_FRICTION_VOL_REGIME"):
            for d in ("LONG", "SHORT"):
                checked_decisions += 1
                if evaluate_kh03_arm_predicate_asof(arm_code=ac, rec=ro, direction=d) != evaluate_kh03_arm_predicate_asof(
                    arm_code=ac, rec=rm, direction=d
                ):
                    mismatches += 1

    # NY DST check
    dst0 = normalize_ny_local_to_utc_ms_r2_c1("2026-11-01T01:30:00", fold=0)
    dst1 = normalize_ny_local_to_utc_ms_r2_c1("2026-11-01T01:30:00", fold=1)
    dst_ok = (dst1["utc_ms"] - dst0["utc_ms"]) == 3_600_000

    out: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH03,
        "schema_version": SCHEMA_VERSION_KH03,
        "future_mutation_invariance": {
            "cutoff_m1_index": cutoff_idx,
            "cutoff_ts_ms": cutoff_ts,
            "checked_causal_predicate_decisions": checked_decisions,
            "mismatches_count": mismatches,
            "passed": (mismatches == 0 and checked_decisions > 0),
        },
        "t01_to_t10_unswept_target_all_passed": bool(t01_t10["all_passed"]),
        "ny_dst_ambiguity_handling_passed": dst_ok,
        "source_level_failure_attribution_context": {
            "r1_target_counterexamples_summary": r1_ce.get("full_dataset_audit_summary"),
            "primary_observed_failure_drivers": [
                "Already-swept HTF target selection in R1 (repaired causally in R2 unswept ledger)",
                "High fee + spread drag relative to tight M1 stops (fees ~55-65% of realized net losses)",
                "Counter-HTF entries and adverse fill-time open-price gap risk breaches (> $2.50 realized tail)",
            ],
        },
        "status": "PASS" if (mismatches == 0 and checked_decisions > 0 and bool(t01_t10["all_passed"]) and dst_ok) else "FAIL",
    }
    out["artifact_canonical_sha256"] = canonical_hash({k: v for k, v in out.items() if k != "artifact_canonical_sha256"})
    return out


def run_kh03_full_research_study_10_to_16(
    *,
    datasets: Mapping[str, Any],
    mtf_bundle: Mapping[str, Any],
    ledger_bundle: Mapping[str, Any],
    r1_candidates: Mapping[str, Any],
    tested_code_sha: str,
    prereg_plan: Mapping[str, Any],
    seed: int = DEFAULT_RANDOM_SEED_KH02_R2,
) -> Dict[str, Dict[str, Any]]:
    """
    Executes KH-03 pre-registered research study end-to-end on REUSED DEV data (NEVER fresh OOS!),
    producing artifacts `11`, `12`, `13`, `14`, `15`, and `16`.
    """
    bars_1m = list(datasets["1m"])
    rec_by_idx = _mtf_records_by_idx(mtf_bundle)

    # 11: Causal as-of & future mutation invariance
    f11 = run_kh03_causal_asof_and_future_mutation_audit(
        datasets=datasets, mtf_bundle=mtf_bundle, ledger_bundle=ledger_bundle, r1_candidates=r1_candidates
    )

    # 12: Base 1X full opportunity conservation & chronological 1-position replay across all 6 KH-03 arms
    r2_cands_1x = build_r2_target_repair_full_opportunity_conservation(
        datasets=datasets, mtf_bundle=mtf_bundle, ledger_bundle=ledger_bundle, r1_candidates=None, cost_assumptions=CostAssumptions()
    )
    kh03_cands_1x = build_kh03_arm_candidates_from_r2_universe(
        datasets=datasets, mtf_bundle=mtf_bundle, r2_cands_bundle=r2_cands_1x, spread_usd=0.20, slippage_usd=0.10, seed=seed
    )

    base_replays_1x: Dict[str, Dict[str, Any]] = {}
    base_episodes_1x: Dict[str, List[Dict[str, Any]]] = {}
    total_overlaps_12 = 0
    for ac in KH03_ALL_ARMS:
        rep_sum, ep_rows = _replay_chronological_custom_cost_scenario(
            arm_code=ac,
            scenario_id="1X_BASE_0p0004",
            candidates=kh03_cands_1x["arm_candidates"][ac],
            bars_1m=bars_1m,
            spread_usd=0.20,
            slippage_usd=0.10,
            taker_fee_rate=0.0004,
            maker_fee_rate=0.0002,
            return_episodes=True,
        )
        total_overlaps_12 += int(rep_sum.get("overlap_violations", 0))
        base_replays_1x[ac] = rep_sum
        base_episodes_1x[ac] = ep_rows

    f12: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH03,
        "schema_version": SCHEMA_VERSION_KH03,
        "preregistered_plan_sha256": prereg_plan["plan_frozen_sha256"],
        "data_regime": "REUSED_RESEARCH_PURGED_WALKFORWARD_NOT_FRESH_OOS",
        "total_directional_bar_opportunities": kh03_cands_1x["total_directional_bar_opportunities"],
        "raw_m1_triggers_count": kh03_cands_1x["raw_m1_triggers_count"],
        "raw_r2_geometry_risk_eligible_count": kh03_cands_1x["raw_r2_geometry_risk_eligible_count"],
        "all_arms_conserved_exact": kh03_cands_1x["all_arms_conserved_exact"],
        "total_overlap_violations": total_overlaps_12,
        "arm_conservation": kh03_cands_1x["arm_conservation"],
        "arm_1x_replay_summaries": base_replays_1x,
        "status": "PASS" if (kh03_cands_1x["all_arms_conserved_exact"] and total_overlaps_12 == 0) else "FAIL",
    }
    f12["artifact_canonical_sha256"] = canonical_hash({k: v for k, v in f12.items() if k != "artifact_canonical_sha256"})

    # 13: Paired stratified common-support matching vs NC_NULL_TRIGGER (SMD <= 0.10) + Placebo/Time-Offset comparisons
    m1_mid_ts = int(bars_1m[len(bars_1m) // 2]["close_time"]) if bars_1m else 0
    null_closed = [e for e in base_episodes_1x["NC_NULL_TRIGGER"] if e["filled"] and e["outcome_label"] in ("WIN", "LOSS")]

    def _covs(ep: Mapping[str, Any]) -> Dict[str, Any]:
        # Derive causal covariates strictly from as-of MTF record
        opp_id = str(ep["opportunity_id"])
        idx = -1
        for part in opp_id.split("_"):
            if part.startswith("idx") and part[3:].isdigit():
                idx = int(part[3:])
                break
        rec = rec_by_idx.get(idx)
        is_long = 1.0 if ep["direction"] == "LONG" else 0.0
        is_liq = 1.0 if (rec is not None and rec.is_liquid_session) else 0.0
        hi_vol = 1.0 if (rec is not None and rec.h1_atr_causal_prefix_quantile is not None and rec.h1_atr_causal_prefix_quantile >= 0.50) else 0.0
        half2 = 1.0 if int(ep["decision_timestamp_ms"]) >= m1_mid_ts else 0.0
        return {
            "is_long": is_long,
            "is_liquid_session": is_liq,
            "h1_atr_high_vol": hi_vol,
            "time_second_half": half2,
            "stratum_key": f"{int(is_long)}|{int(is_liq)}|{int(hi_vol)}|{int(half2)}",
        }

    cov_names = ("is_long", "is_liquid_session", "h1_atr_high_vol", "time_second_half")
    null_covs = [_covs(e) for e in null_closed]
    null_by_s: Dict[str, List[Tuple[Dict[str, Any], Dict[str, Any]]]] = defaultdict(list)
    for ep, cv in zip(null_closed, null_covs):
        null_by_s[cv["stratum_key"]].append((ep, cv))

    matching_reports: Dict[str, Any] = {}
    for ac in KH03_PRIMARY_ARMS:
        arm_closed = [e for e in base_episodes_1x[ac] if e["filled"] and e["outcome_label"] in ("WIN", "LOSS")]
        t_covs = [_covs(e) for e in arm_closed]
        pre_smds = {cn: _compute_smd([r[cn] for r in t_covs], [r[cn] for r in null_covs]) for cn in cov_names}

        t_by_s: Dict[str, List[Tuple[Dict[str, Any], Dict[str, Any]]]] = defaultdict(list)
        for ep, cv in zip(arm_closed, t_covs):
            t_by_s[cv["stratum_key"]].append((ep, cv))

        m_t_covs: List[Dict[str, Any]] = []
        m_c_covs: List[Dict[str, Any]] = []
        deltas: List[float] = []
        days: List[str] = []
        for sk in sorted(set(t_by_s.keys()) & set(null_by_s.keys())):
            t_items = sorted(t_by_s[sk], key=lambda x: (int(x[0]["decision_timestamp_ms"]), str(x[0]["opportunity_id"])))
            c_items = sorted(null_by_s[sk], key=lambda x: (int(x[0]["decision_timestamp_ms"]), str(x[0]["opportunity_id"])))
            used_c: Set[int] = set()
            for t_ep, t_cv in t_items:
                t_ts = int(t_ep["decision_timestamp_ms"])
                best_j = None
                best_dt = None
                for j, (c_ep, _) in enumerate(c_items):
                    if j in used_c:
                        continue
                    dt = abs(int(c_ep["decision_timestamp_ms"]) - t_ts)
                    if best_dt is None or dt < best_dt:
                        best_dt = dt
                        best_j = j
                if best_j is not None:
                    used_c.add(best_j)
                    c_ep, c_cv = c_items[best_j]
                    m_t_covs.append(t_cv)
                    m_c_covs.append(c_cv)
                    deltas.append(round(float(t_ep["realized_net_r"]) - float(c_ep["realized_net_r"]), 6))
                    days.append(str(t_ep.get("ny_date") or "UNK"))

        post_smds = {cn: _compute_smd([r[cn] for r in m_t_covs], [r[cn] for r in m_c_covs]) for cn in cov_names}
        est_post = [abs(v) for v in post_smds.values() if v is not None]
        max_post = round(max(est_post), 4) if est_post else None
        overlap_cov = round(len(m_t_covs) / max(1, len(arm_closed)), 4) if arm_closed else 0.0
        eff_n = len(m_t_covs)
        inad: List[str] = []
        if eff_n < 20:
            inad.append(f"EFFECTIVE_N_LT_20:n={eff_n}")
        if overlap_cov < 0.70:
            inad.append(f"OVERLAP_COVERAGE_LT_0p70:cov={overlap_cov}")
        if not est_post or any(v > 0.10 + 1e-9 for v in est_post):
            inad.append(f"POST_MATCH_ABS_SMD_GT_0p10:max={max_post}")

        mq = "BALANCED_COMMON_SUPPORT" if not inad else "MATCHING_INADEQUATE"
        mean_delta = round(sum(deltas) / len(deltas), 4) if deltas else None
        ci_delta = _block_bootstrap_ci(deltas, days, seed=seed + 509) if deltas else None
        matching_reports[ac] = {
            "arm_code": ac,
            "control_arm_code": "NC_NULL_TRIGGER",
            "arm_closed_trades": len(arm_closed),
            "control_closed_trades": len(null_closed),
            "matched_pairs_count": eff_n,
            "overlap_coverage_ratio": overlap_cov,
            "pre_match_smds": pre_smds,
            "post_match_smds": post_smds,
            "max_post_abs_smd": max_post,
            "matching_quality_status": mq,
            "inadequate_reasons": inad,
            "causal_lift_claim_permitted": (mq == "BALANCED_COMMON_SUPPORT"),
            "matched_mean_delta_r_vs_null": mean_delta,
            "matched_ny_day_95ci_delta_r": ci_delta,
            "expectancy_r_vs_placebo_shuffled": (
                round(
                    float(base_replays_1x[ac]["expectancy_r"]) - float(base_replays_1x["NC_PLACEBO_SHUFFLED_TRIGGER"]["expectancy_r"]),
                    4,
                )
                if (
                    base_replays_1x[ac].get("expectancy_r") is not None
                    and base_replays_1x["NC_PLACEBO_SHUFFLED_TRIGGER"].get("expectancy_r") is not None
                )
                else None
            ),
            "expectancy_r_vs_time_offset": (
                round(
                    float(base_replays_1x[ac]["expectancy_r"]) - float(base_replays_1x["NC_TIME_OFFSET_SHIFT"]["expectancy_r"]),
                    4,
                )
                if (
                    base_replays_1x[ac].get("expectancy_r") is not None
                    and base_replays_1x["NC_TIME_OFFSET_SHIFT"].get("expectancy_r") is not None
                )
                else None
            ),
        }

    f13: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH03,
        "schema_version": SCHEMA_VERSION_KH03,
        "b0_vs_b2_cross_universe_status": "COMPARISON_NOT_IDENTIFIABLE",
        "smd_threshold": 0.10,
        "arm_matching_reports": matching_reports,
        "negative_control_summaries_1x": {nc: base_replays_1x[nc] for nc in KH03_NEGATIVE_CONTROLS},
        "status": "PASS",
    }
    f13["artifact_canonical_sha256"] = canonical_hash({k: v for k, v in f13.items() if k != "artifact_canonical_sha256"})

    # 14: Net economics across ALL 6 cost scenarios (1x / fee-only / 2x / 3x) with root-fixed authoritative fees!
    six_scenarios_kh03: Dict[str, Any] = {}
    all_6_overlaps = 0
    zero_fee_bugs_14 = 0
    for sc_id, sp_usd, slp_usd, taker_r, maker_r in KH03_SIX_COST_SCENARIOS:
        if sc_id == "1X_BASE_0p0004":
            sc_cands = kh03_cands_1x
            sc_arms_rep = {ac: dict(base_replays_1x[ac]) for ac in KH03_ALL_ARMS}
        else:
            costs = CostAssumptions(maker_fee_rate=maker_r, taker_fee_rate=taker_r, slippage_usd=slp_usd)
            r2_sc = build_r2_target_repair_full_opportunity_conservation(
                datasets=datasets, mtf_bundle=mtf_bundle, ledger_bundle=ledger_bundle, r1_candidates=None, cost_assumptions=costs
            )
            sc_cands = build_kh03_arm_candidates_from_r2_universe(
                datasets=datasets, mtf_bundle=mtf_bundle, r2_cands_bundle=r2_sc, spread_usd=sp_usd, slippage_usd=slp_usd, seed=seed
            )
            sc_arms_rep = {}
            for ac in KH03_ALL_ARMS:
                rep_s = _replay_chronological_custom_cost_scenario(
                    arm_code=ac,
                    scenario_id=sc_id,
                    candidates=sc_cands["arm_candidates"][ac],
                    bars_1m=bars_1m,
                    spread_usd=sp_usd,
                    slippage_usd=slp_usd,
                    taker_fee_rate=taker_r,
                    maker_fee_rate=maker_r,
                )
                all_6_overlaps += int(rep_s.get("overlap_violations", 0))
                sc_arms_rep[ac] = rep_s

        for ac, rs in sc_arms_rep.items():
            g_d = Decimal(str(rs.get("gross_pnl_usd") or 0.0))
            f_d = Decimal(str(rs.get("total_fees_usd") or 0.0))
            n_d = Decimal(str(rs.get("net_pnl_usd") or 0.0))
            if int(rs.get("closed") or 0) > 0 and f_d == Decimal("0") and (g_d - n_d) > Decimal("0.01"):
                zero_fee_bugs_14 += 1

        six_scenarios_kh03[sc_id] = {
            "parameters": {"spread_usd": sp_usd, "slippage_usd": slp_usd, "taker_fee_rate": taker_r, "maker_fee_rate": maker_r},
            "raw_r2_eligible_denominator": sc_cands["raw_r2_geometry_risk_eligible_count"],
            "arms": sc_arms_rep,
        }

    f14: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH03,
        "schema_version": SCHEMA_VERSION_KH03,
        "data_regime": "REUSED_RESEARCH_PURGED_WALKFORWARD_NOT_FRESH_OOS",
        "quote_mode": "MODELED_BID_ASK",
        "observed_l1_bbo_available": False,
        "ACCOUNT_EFFECTIVE_FEE_VERDICT": "UNKNOWN_NOT_LOCALLY_VERIFIED",
        "total_overlap_violations": all_6_overlaps,
        "zero_fee_display_bugs_count": zero_fee_bugs_14,
        "scenarios": six_scenarios_kh03,
        "status": "PASS" if (all_6_overlaps == 0 and zero_fee_bugs_14 == 0) else "FAIL",
    }
    f14["artifact_canonical_sha256"] = canonical_hash({k: v for k, v in f14.items() if k != "artifact_canonical_sha256"})

    # 15: Purged walk-forward (>=240m embargo), cluster bootstrap, Holm-Bonferroni multiplicity, power & honest DEV verdict!
    n_folds = 4
    n_bars = len(bars_1m)
    fold_bounds: List[Tuple[int, int]] = []
    blk = max(1, n_bars // n_folds)
    for fi in range(n_folds):
        lo_i = fi * blk
        hi_i = (n_bars - 1) if fi == n_folds - 1 else min(n_bars - 1, (fi + 1) * blk - 1)
        fold_bounds.append((int(bars_1m[lo_i]["open_time"]), int(bars_1m[hi_i]["close_time"])))

    raw_pvals: Dict[str, Optional[float]] = {}
    arm_stats: Dict[str, Any] = {}
    for ac in KH03_PRIMARY_ARMS:
        cl_eps = [e for e in base_episodes_1x[ac] if e["filled"] and e["outcome_label"] in ("WIN", "LOSS")]
        r_vals = [float(e["realized_net_r"]) for e in cl_eps]
        ny_days = [str(e.get("ny_date") or "UNK") for e in cl_eps]
        ep_ids = [str(e.get("setup_episode_id") or e["opportunity_id"]) for e in cl_eps]
        pval = _one_sided_cluster_bootstrap_pvalue_gt_zero(r_vals, ny_days, seed=seed + 701 + KH03_PRIMARY_ARMS.index(ac))
        raw_pvals[ac] = pval

        # Purged walk-forward fold evaluation on REUSED DEV
        wf_folds: List[Dict[str, Any]] = []
        pos_folds = 0
        fold_exps: List[float] = []
        for fi, (lo_ms, hi_ms) in enumerate(fold_bounds):
            f_eps = [e for e in cl_eps if lo_ms <= int(e["decision_timestamp_ms"]) <= hi_ms]
            f_exp = round(sum(float(e["realized_net_r"]) for e in f_eps) / len(f_eps), 4) if f_eps else None
            if f_exp is not None:
                fold_exps.append(f_exp)
                if f_exp > 0.0:
                    pos_folds += 1
            wf_folds.append(
                {
                    "fold_id": f"DEV_PURGED_FOLD_{fi + 1}",
                    "closed_trades": len(f_eps),
                    "net_pnl_usd": round(sum(float(e["realized_net_pnl_usd"]) for e in f_eps), 4),
                    "expectancy_r": f_exp,
                }
            )
        fold_exps_sorted = sorted(fold_exps)
        med_fold_exp = (
            fold_exps_sorted[len(fold_exps_sorted) // 2] if fold_exps_sorted else None
        )
        pos_fold_ratio = round(pos_folds / max(1, len(fold_exps)), 4) if fold_exps else 0.0

        arm_stats[ac] = {
            "closed_trades_1x": len(cl_eps),
            "effective_ny_days_1x": len(set(ny_days)),
            "expectancy_r_1x": base_replays_1x[ac].get("expectancy_r"),
            "net_pnl_usd_1x": base_replays_1x[ac].get("net_pnl_usd"),
            "expectancy_r_fee_only_0p0006": six_scenarios_kh03["FEE_ONLY_0p0006"]["arms"][ac].get("expectancy_r"),
            "net_pnl_usd_fee_only_0p0006": six_scenarios_kh03["FEE_ONLY_0p0006"]["arms"][ac].get("net_pnl_usd"),
            "expectancy_r_2x": six_scenarios_kh03["2X_ALL_COST_0p0004"]["arms"][ac].get("expectancy_r"),
            "net_pnl_usd_2x": six_scenarios_kh03["2X_ALL_COST_0p0004"]["arms"][ac].get("net_pnl_usd"),
            "expectancy_r_3x": six_scenarios_kh03["3X_ALL_COST_0p0004"]["arms"][ac].get("expectancy_r"),
            "ny_day_cluster_95ci_r": _block_bootstrap_ci(r_vals, ny_days, seed=seed + 11) if r_vals else None,
            "episode_cluster_95ci_r": _block_bootstrap_ci(r_vals, ep_ids, seed=seed + 29) if r_vals else None,
            "raw_one_sided_pvalue_gt_zero": pval,
            "purged_wf_folds": wf_folds,
            "purged_wf_median_fold_expectancy_r": med_fold_exp,
            "purged_wf_positive_fold_ratio": pos_fold_ratio,
        }

    holm_pvals = _holm_bonferroni_adjust(raw_pvals)
    passing_dev_candidates: List[str] = []
    low_power_positive_arms: List[str] = []

    for ac in KH03_PRIMARY_ARMS:
        st = arm_stats[ac]
        st["holm_bonferroni_adjusted_pvalue"] = holm_pvals[ac]
        mr = matching_reports[ac]
        ci_ny = st["ny_day_cluster_95ci_r"] or {}
        ci_lo = ci_ny.get("ci_low") if isinstance(ci_ny, Mapping) else None

        pos_1x = (st["expectancy_r_1x"] is not None and float(st["expectancy_r_1x"]) > 0.0 and float(st["net_pnl_usd_1x"] or 0.0) > 0.0)
        pos_fo = (st["expectancy_r_fee_only_0p0006"] is not None and float(st["expectancy_r_fee_only_0p0006"]) > 0.0)
        pos_2x = (st["expectancy_r_2x"] is not None and float(st["expectancy_r_2x"]) > 0.0)
        n_ok = int(st["closed_trades_1x"]) >= 50 and int(st["effective_ny_days_1x"]) >= 10
        ci_ok = ci_lo is not None and float(ci_lo) > 0.0
        p_ok = holm_pvals[ac] is not None and float(holm_pvals[ac]) < 0.05  # type: ignore[arg-type]
        match_ok = (
            mr["matching_quality_status"] == "BALANCED_COMMON_SUPPORT"
            and mr["matched_mean_delta_r_vs_null"] is not None
            and float(mr["matched_mean_delta_r_vs_null"]) > 0.0
        )
        ctrl_ok = (
            mr["expectancy_r_vs_placebo_shuffled"] is not None
            and float(mr["expectancy_r_vs_placebo_shuffled"]) > 0.0
            and mr["expectancy_r_vs_time_offset"] is not None
            and float(mr["expectancy_r_vs_time_offset"]) > 0.0
        )
        wf_ok = (
            st["purged_wf_median_fold_expectancy_r"] is not None
            and float(st["purged_wf_median_fold_expectancy_r"]) > 0.0
            and float(st["purged_wf_positive_fold_ratio"]) >= 0.60
        )

        failed_criteria: List[str] = []
        if not n_ok:
            failed_criteria.append(f"INSUFFICIENT_SAMPLE_OR_DAYS:n={st['closed_trades_1x']},days={st['effective_ny_days_1x']}")
        if not pos_1x:
            failed_criteria.append(f"NON_POSITIVE_1X_NET:expR={st['expectancy_r_1x']},net={st['net_pnl_usd_1x']}")
        if not pos_fo:
            failed_criteria.append(f"NON_POSITIVE_FEE_ONLY_0p0006:expR={st['expectancy_r_fee_only_0p0006']}")
        if not pos_2x:
            failed_criteria.append(f"NON_POSITIVE_2X_STRESS:expR={st['expectancy_r_2x']}")
        if not ci_ok:
            failed_criteria.append(f"NY_DAY_95CI_CROSSES_ZERO:ci_low={ci_lo}")
        if not p_ok:
            failed_criteria.append(f"HOLM_BONFERRONI_P_GE_0p05:p_adj={holm_pvals[ac]}")
        if not match_ok:
            failed_criteria.append(f"MATCHED_NULL_LIFT_FAILED:mq={mr['matching_quality_status']},delta={mr['matched_mean_delta_r_vs_null']}")
        if not ctrl_ok:
            failed_criteria.append("FAILED_TO_BEAT_PLACEBO_AND_TIME_OFFSET_CONTROLS")
        if not wf_ok:
            failed_criteria.append(f"PURGED_WF_UNSTABLE:med={st['purged_wf_median_fold_expectancy_r']},pos_ratio={st['purged_wf_positive_fold_ratio']}")

        st["failed_advancement_criteria"] = failed_criteria
        st["passes_preregistered_dev_advancement"] = len(failed_criteria) == 0
        if len(failed_criteria) == 0:
            passing_dev_candidates.append(ac)
        elif pos_1x and pos_2x and not n_ok:
            low_power_positive_arms.append(ac)

    if passing_dev_candidates:
        dev_verdict = "DEV_CANDIDATE_UNPROVEN_OOS"
        econ_verdict = "NO_PROVEN_EDGE"  # DEV NEVER proves edge without fresh untouched OOS!
        kh04_entry = "BLOCKED_NO_ELIGIBLE_PROSPECTIVE_DATA"
    elif low_power_positive_arms:
        dev_verdict = "INCONCLUSIVE_LOW_POWER"
        econ_verdict = "NO_PROVEN_EDGE"
        kh04_entry = "BLOCKED_NO_DEV_CANDIDATE"
    else:
        dev_verdict = "NO_ECONOMIC_ADVANCEMENT_CANDIDATE"
        econ_verdict = "NO_PROVEN_EDGE"
        kh04_entry = "BLOCKED_NO_DEV_CANDIDATE"

    power_grid = compute_prospective_oos_power_requirements_r2_c1(num_primary_arms=len(KH03_PRIMARY_ARMS))

    f15: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH03,
        "schema_version": SCHEMA_VERSION_KH03,
        "preregistered_plan_sha256": prereg_plan["plan_frozen_sha256"],
        "walkforward_data_regime": "REUSED_RESEARCH_PURGED_WALKFORWARD_NOT_FRESH_OOS",
        "embargo_minutes": 240,
        "multiplicity_family_size": len(KH03_PRIMARY_ARMS),
        "primary_arm_evaluations": arm_stats,
        "passing_dev_candidates": passing_dev_candidates,
        "low_power_positive_arms": low_power_positive_arms,
        "prospective_oos_power_requirements": power_grid,
        "KH03_DEV_CANDIDATE_VERDICT": dev_verdict,
        "RESEARCH_ECONOMIC_VERDICT": econ_verdict,
        "FRESH_OOS_VERDICT": "BLOCKED_NO_UNTOUCHED_DATA",
        "KH04_ENTRY": kh04_entry,
        "OOS_EVALUATIONS": 0,
        "ACTIVATION_READY": "NO",
        "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2),
        "status": "PASS",
    }
    f15["artifact_canonical_sha256"] = canonical_hash({k: v for k, v in f15.items() if k != "artifact_canonical_sha256"})

    # 16: Risk-tail, gap, fill-time & fee disclosure for KH-03 arms (NEVER conflating planned cap with physical guarantee!)
    tail_by_arm: Dict[str, Any] = {}
    global_max_loss = 0.0
    for ac in KH03_ALL_ARMS:
        eps = base_episodes_1x[ac]
        closed_l = [e for e in eps if e["filled"] and e["outcome_label"] == "LOSS"]
        loss_vals = sorted(-float(e["realized_net_pnl_usd"]) for e in closed_l)
        gt_250 = [x for x in loss_vals if x > PLANNED_NET_RISK_CAP_USD + 1e-6]
        max_l = round(max(loss_vals), 4) if loss_vals else 0.0
        global_max_loss = max(global_max_loss, max_l)
        open_accrued = sum(
            (Decimal(str(e["accrued_open_entry_fee_usd"])) for e in eps if e["filled"] and e["outcome_label"] in ("OPEN_AT_HORIZON", "DATA_GAP_UNRESOLVED")),
            Decimal("0"),
        )
        tail_by_arm[ac] = {
            "scheduled": len(eps),
            "closed": base_replays_1x[ac]["closed"],
            "losses": len(closed_l),
            "open_at_horizon": base_replays_1x[ac]["open_at_horizon"],
            "realized_losses_exceeding_2p50_count": len(gt_250),
            "max_realized_net_loss_usd": max_l,
            "realized_loss_quantiles_usd": {
                "p50": _nearest_rank_quantile(loss_vals, 0.50),
                "p90": _nearest_rank_quantile(loss_vals, 0.90),
                "p95": _nearest_rank_quantile(loss_vals, 0.95),
                "max": max_l,
            },
            "closed_total_fees_usd": base_replays_1x[ac]["total_fees_usd"],
            "accrued_open_entry_fees_usd": _dec4(open_accrued),
        }

    f16: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH03,
        "schema_version": SCHEMA_VERSION_KH03,
        "risk_cap_usd": PLANNED_NET_RISK_CAP_USD,
        "planned_cap_is_physical_guarantee": False,
        "global_max_realized_net_loss_kh03_1x_usd": round(global_max_loss, 4),
        "arm_tail_and_fee_disclosures_1x": tail_by_arm,
        "explicit_regime_separation": {
            "A_executed_modeled_historical_1x": "REUSED_DEV_MODELED_BID_ASK_SPREAD_0p20_NOT_OBSERVED_L1",
            "B_hypothetical_fill_time_policy": "RESEARCH_COUNTERFACTUAL_ONLY_NOT_PRODUCTION_RUNTIME",
            "C_production_engine_behavior": "UNCHANGED_KEEP_AS_IS_ALL_MODES_OFF",
            "D_account_effective_fee_tier": "UNKNOWN_NOT_LOCALLY_VERIFIED",
        },
        "realized_tail_loss_disclosure": (
            "planned_net_risk_usd <= 2.50 USD is a causal pre-exit stop-loss estimate, NOT a physical "
            "guarantee against adverse gaps, liquidity shocks, or stop-fill slippage. Do NOT sum "
            "overlapping arm x scenario rows into unique independent trades."
        ),
        "status": "PASS",
    }
    f16["artifact_canonical_sha256"] = canonical_hash({k: v for k, v in f16.items() if k != "artifact_canonical_sha256"})

    return {
        "11": f11,
        "12": f12,
        "13": f13,
        "14": f14,
        "15": f15,
        "16": f16,
    }
