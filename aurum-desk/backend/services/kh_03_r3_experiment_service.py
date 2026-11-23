"""
aurum-desk/backend/services/kh_03_r3_experiment_service.py

AURUM DESK — KH-03.R3 | P1-C BOUNDED PRE-REGISTERED DEV RESEARCH (E1, E2, E3)
SPEC VERSION: KH-03.R3-v1.0 | RESEARCH ONLY (REUSED DEV) | OOS_EVALUATIONS=0 | ACTIVATION_READY=NO

Implements 3 distinct, falsifiable, strictly as-of research mechanisms derived strictly from
TRAIN-only forensic diagnosis (never renaming H1/H2, never promoting H3):
  - `KH03_R3_E1_LOW_FRICTION_STOP_FLOOR`: pure causal entry-quality filter (`stop_dist >= 4.00 USD`,
    `m15_trend_dir == dir`, `stop_dist / m1_atr_14 >= 1.25`) addressing tight M1 stop vs taker fee drag.
  - `KH03_R3_E2_CAUSAL_DELAYED_CONFIRMATION`: strictly post-observation closed-bar breakout
    confirmation (`1 <= wait_bars <= 6`, `new_decision_ts > base_decision_ts`, entry strictly on
    `bar[idx + wait + 1].open`, NEVER `OPEN_TO_OPEN`!), full re-risked under `$2.50` cap & `net_rr >= 2.0`.
    Explicitly labeled `MODELED_RESEARCH_COUNTERFACTUAL` (`MODELED_DELAYED_FILL_NOT_OBSERVED_L1`).
  - `KH03_R3_E3_CAUSAL_NEAREST_M5_UNSWEPT_EXIT`: strictly causal as-of 5-bar fractal M5 unswept
    swing liquidity target (nearest structural target achieving `net_rr >= 2.0` & `planned_risk <= 2.50`
    on the EXACT same entry & SL, falling back to baseline R2 target if none closer exists!).
    Explicitly labeled `MODELED_RESEARCH_COUNTERFACTUAL` (`MODELED_BID_ASK`, not observed L1).

Evaluated exclusively on R2's strictly chronological expanding forward-only DEV folds
(embargo >= 240m, label exit purge, fold-local past-only selection frozen BEFORE validation,
fresh bounded per-fold validation replays), all 6 cost scenarios (dynamic re-risk vs fixed-fills),
Holm-Bonferroni (m=3), and R2's strict CI/DEV gate (`evaluate_kh03_dev_ci_and_advancement`).
"""
from __future__ import annotations

from collections import Counter, defaultdict
import copy
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import random
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from domain_calculator import CostAssumptions, calculate_risk_reward
from services.kh_02_r2_prospective_data_service import (
    compute_prospective_oos_power_requirements_r2_c1,
)
from services.kh_02_r2_research_integrity_service import (
    CANONICAL_DATASET_SHA256_KH02_R2,
    DEFAULT_MODELED_SPREAD_USD,
    DEFAULT_RANDOM_SEED_KH02_R2,
    DEFAULT_SLIPPAGE_USD,
    MIN_NET_RR_THRESHOLD,
    PLANNED_NET_RISK_CAP_USD,
    RUNTIME_SAFETY_MODES_KH02_R2,
    _block_bootstrap_ci,
    _compute_smd,
    _replay_chronological_custom_cost_scenario,
    _summarize_episodes,
    build_r2_target_repair_full_opportunity_conservation,
)
from services.kh_03_r2_methodology_service import (
    MAX_SIM_BAR_HORIZON_MINUTES_KH03_R2,
    MIN_EMBARGO_MINUTES_KH03_R2,
    _replay_chronological_window_bounded,
    build_kh03_r2_forward_only_dev_folds,
    evaluate_kh03_dev_ci_and_advancement,
    parse_kh03_cluster_ci_strict,
    select_past_only_arm_for_fold,
    validate_kh03_r2_fold_causality,
)
from services.kh_03_r3_legacy_research_guard import (
    SCHEMA_VERSION_KH03_R3,
    SPEC_VERSION_KH03_R3,
)
from services.kh_03_research_service import (
    KH03_NEGATIVE_CONTROLS,
    KH03_SIX_COST_SCENARIOS,
    _holm_bonferroni_adjust,
    _mtf_records_by_idx,
    _one_sided_cluster_bootstrap_pvalue_gt_zero,
)
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash

KH03_R3_PRIMARY_ARMS: Tuple[str, ...] = (
    "KH03_R3_E1_LOW_FRICTION_STOP_FLOOR",
    "KH03_R3_E2_CAUSAL_DELAYED_CONFIRMATION",
    "KH03_R3_E3_CAUSAL_NEAREST_M5_UNSWEPT_EXIT",
)
KH03_R3_ALL_ARMS: Tuple[str, ...] = (*KH03_R3_PRIMARY_ARMS, *KH03_NEGATIVE_CONTROLS)

E1_MIN_STOP_DIST_USD = 4.00
E1_MIN_STOP_TO_M1_ATR_RATIO = 1.25
E2_MAX_WAIT_CLOSED_BARS = 6


def _dec4(v: Decimal) -> float:
    return float(v.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def build_r3_causal_m5_unswept_target_ledger(
    *,
    datasets: Mapping[str, Any],
    pivot_radius: int = 2,
) -> Dict[str, Any]:
    """
    Builds a strictly causal as-of 5-bar fractal M5 swing-high / swing-low unswept target
    index (`known_at_ms = bars_5m[p + pivot_radius]['close_time']`).
    A swing level is tracked as swept at the FIRST M1 bar strictly AFTER confirmation whose
    high >= level_price (for SWING_HIGH) or low <= level_price (for SWING_LOW).
    NEVER peeks past `decision_timestamp_ms`.
    """
    bars_5m = list(datasets.get("5m") or ())
    bars_1m = list(datasets.get("1m") or ())
    r = max(1, int(pivot_radius))

    m1_closes = [int(b["close_time"]) for b in bars_1m]
    m1_highs = [float(b["high"]) for b in bars_1m]
    m1_lows = [float(b["low"]) for b in bars_1m]
    n_m1 = len(bars_1m)

    # Confirm M5 swings strictly at bar p + r close_time
    import bisect

    swings_high: List[Tuple[int, float, int]] = []  # (known_at_ms, price, first_swept_m1_idx_or_n)
    swings_low: List[Tuple[int, float, int]] = []

    m1_first_ms = int(bars_1m[0]["open_time"]) if bars_1m else 0
    m1_last_ms = int(bars_1m[-1]["close_time"]) if bars_1m else 0
    # Only keep swings confirmed within [m1_first_ms - 48h, m1_last_ms] for fast O(N) scan
    lookback_start_ms = m1_first_ms - 48 * 3_600_000

    for p in range(r, len(bars_5m) - r):
        conf_bar = bars_5m[p + r]
        known_ms = int(conf_bar["close_time"])
        if known_ms < lookback_start_ms or known_ms > m1_last_ms:
            continue
        p_hi = float(bars_5m[p]["high"])
        p_lo = float(bars_5m[p]["low"])
        is_hi = all(p_hi >= float(bars_5m[p + k]["high"]) for k in range(-r, r + 1) if k != 0)
        is_lo = all(p_lo <= float(bars_5m[p + k]["low"]) for k in range(-r, r + 1) if k != 0)
        if not (is_hi or is_lo):
            continue

        # Check if already swept on M5 BEFORE M1 window started, or find first M1 sweep index
        start_m1 = bisect.bisect_right(m1_closes, known_ms)
        if is_hi:
            swept_before_m1 = False
            for j in range(p + r + 1, len(bars_5m)):
                if int(bars_5m[j]["open_time"]) >= m1_first_ms:
                    break
                if float(bars_5m[j]["high"]) >= p_hi:
                    swept_before_m1 = True
                    break
            if not swept_before_m1:
                sw_i = n_m1
                for mi in range(start_m1, n_m1):
                    if m1_highs[mi] >= p_hi:
                        sw_i = mi
                        break
                swings_high.append((known_ms, round(p_hi, 2), sw_i))
        if is_lo:
            swept_before_m1 = False
            for j in range(p + r + 1, len(bars_5m)):
                if int(bars_5m[j]["open_time"]) >= m1_first_ms:
                    break
                if float(bars_5m[j]["low"]) <= p_lo:
                    swept_before_m1 = True
                    break
            if not swept_before_m1:
                sw_i = n_m1
                for mi in range(start_m1, n_m1):
                    if m1_lows[mi] <= p_lo:
                        sw_i = mi
                        break
                swings_low.append((known_ms, round(p_lo, 2), sw_i))

    return {
        "pivot_radius": r,
        "swings_high": swings_high,
        "swings_low": swings_low,
        "total_m5_swings_tracked": len(swings_high) + len(swings_low),
    }


def query_nearest_causal_unswept_m5_target(
    *,
    m5_ledger: Mapping[str, Any],
    direction: str,
    decision_idx_1m: int,
    decision_ts_ms: int,
    entry_price: float,
    selected_sl: float,
    baseline_tp: float,
    costs: CostAssumptions,
    min_net_rr: float = MIN_NET_RR_THRESHOLD,
    risk_cap_usd: float = PLANNED_NET_RISK_CAP_USD,
) -> Dict[str, Any]:
    """
    Strictly causal as-of lookup of the NEAREST unswept M5 swing target (`known_ms <= decision_ts_ms`
    and `first_swept_m1_idx > decision_idx_1m`) closer than `baseline_tp` that satisfies
    `calculate_risk_reward(..., net_rr >= min_net_rr, net_risk <= risk_cap_usd, qty >= 0.01)`.
    Falls back cleanly to `baseline_tp` if no closer structural unswept M5 level qualifies.
    """
    d = str(direction).upper()
    pool = m5_ledger["swings_high"] if d == "LONG" else m5_ledger["swings_low"]
    cands: List[float] = []
    for known_ms, px, sw_i in pool:
        if known_ms > decision_ts_ms:
            continue
        if sw_i <= decision_idx_1m:
            continue
        if d == "LONG" and entry_price < px < baseline_tp:
            cands.append(px)
        elif d == "SHORT" and baseline_tp < px < entry_price:
            cands.append(px)

    cands_sorted = sorted(set(cands)) if d == "LONG" else sorted(set(cands), reverse=True)
    for px in cands_sorted:
        cr = calculate_risk_reward(
            direction=d,
            planned_entry=float(entry_price),
            stop_loss=float(selected_sl),
            take_profit=float(px),
            capital_usdt=1000.0,
            risk_pct=0.25,
            leverage=20,
            margin_mode="ISOLATED",
            min_net_rr=min_net_rr,
            costs=costs,
            entry_has_slippage=False,
            now_ms=decision_ts_ms,
        )
        if cr.is_valid and cr.can_execute and float(cr.quantity) >= 0.01 - 1e-9 and float(cr.net_risk_usdt) <= risk_cap_usd + 1e-9 and float(cr.net_rr) >= min_net_rr - 1e-9:
            return {
                "selected_tp": float(px),
                "target_source": "CAUSAL_NEAREST_M5_UNSWEPT_SWING",
                "replaced_baseline_tp": True,
                "new_net_rr": round(float(cr.net_rr), 4),
                "new_qty": round(float(cr.quantity), 4),
                "new_planned_risk_usd": round(float(cr.net_risk_usdt), 4),
            }

    return {
        "selected_tp": float(baseline_tp),
        "target_source": "RETAINED_R2_BASELINE_HTF_UNSWEPT",
        "replaced_baseline_tp": False,
        "new_net_rr": None,
        "new_qty": None,
        "new_planned_risk_usd": None,
    }


def freeze_r3_bounded_research_plan(
    *,
    tested_code_sha: str,
    dataset_sha256: str = CANONICAL_DATASET_SHA256_KH02_R2,
    random_seed: int = DEFAULT_RANDOM_SEED_KH02_R2,
) -> Dict[str, Any]:
    """
    Artifact 11 (`11_training_only_bounded_prereg_plan.json`):
    Frozen & SHA-256 hashed BEFORE running any KH-03.R3 validation outcome replay.
    """
    created_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    hypotheses = [
        {
            "arm_code": "KH03_R3_E1_LOW_FRICTION_STOP_FLOOR",
            "axis": "E1_ENTRY_QUALITY_FRICTION_STOP_FLOOR",
            "fidelity_tier": "MODELED_RESEARCH_COUNTERFACTUAL",
            "observable_at": "decision_timestamp_ms (strictly as-of M1/M15 close)",
            "alters_entry_sl_tp_qty": False,
            "fixed_parameters": {
                "min_stop_dist_usd": E1_MIN_STOP_DIST_USD,
                "min_stop_to_m1_atr_ratio": E1_MIN_STOP_TO_M1_ATR_RATIO,
                "require_m15_trend_alignment": True,
            },
            "causal_mechanism": (
                "Derived from TRAIN diagnosis showing median SL=2.38 USD lets taker fees eat 56-83% of risk. "
                "Requires structural SL >= 4.00 USD, SL >= 1.25 * M1_ATR_14, and as-of M15 trend agreement."
            ),
            "falsification_rule": "Falsified if R2 strict DEV gate fails on 1x/fee-only/2x, strict CI, Holm m=3, controls, or forward-only WF.",
        },
        {
            "arm_code": "KH03_R3_E2_CAUSAL_DELAYED_CONFIRMATION",
            "axis": "E2_ENTRY_TIMING_CLOSED_BAR_DELAY",
            "fidelity_tier": "MODELED_RESEARCH_COUNTERFACTUAL",
            "execution_realism_label": "MODELED_DELAYED_FILL_NOT_OBSERVED_L1",
            "observable_at": "new_decision_timestamp_ms = bars_1m[idx + wait_bars].close_time > base_decision_ts; fill on bar[idx + wait_bars + 1].open",
            "alters_entry_sl_tp_qty": True,
            "fixed_parameters": {"min_wait_closed_bars": 1, "max_wait_closed_bars": E2_MAX_WAIT_CLOSED_BARS},
            "causal_mechanism": (
                "Waits 1..6 strictly closed M1 bars for directional close breakout beyond base trigger bar "
                "without hitting SL, then re-risks causally at new close and enters on the NEXT open (never OPEN_TO_OPEN)."
            ),
            "falsification_rule": "Falsified if R2 strict DEV gate fails or fails to beat NULL / placebo / time-shift.",
        },
        {
            "arm_code": "KH03_R3_E3_CAUSAL_NEAREST_M5_UNSWEPT_EXIT",
            "axis": "E3_EXIT_MECHANISM_NEAREST_CAUSAL_M5_UNSWEPT_TARGET",
            "fidelity_tier": "MODELED_RESEARCH_COUNTERFACTUAL",
            "execution_realism_label": "MODELED_BID_ASK_NOT_OBSERVED_L1",
            "observable_at": "decision_timestamp_ms (strictly confirmed as-of M5/M15/H1 unswept swing)",
            "alters_entry_sl_tp_qty": True,
            "fixed_parameters": {"m5_pivot_radius": 2, "min_net_rr": MIN_NET_RR_THRESHOLD, "risk_cap_usd": PLANNED_NET_RISK_CAP_USD},
            "causal_mechanism": (
                "Derived from TRAIN diagnosis showing median TP=24.05 USD (8.92x SL) causes 43.9% of losses "
                "to reach >=1.0x SL favorably before stopping out. Selects nearest strictly as-of unswept M5 swing "
                "satisfying net_rr >= 2.0 on exact same entry/SL (fallback to baseline R2 target)."
            ),
            "falsification_rule": "Falsified if paired/1-pos replay fails R2 strict DEV gate or fails to beat controls.",
        },
    ]

    plan: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH03_R3,
        "schema_version": SCHEMA_VERSION_KH03_R3,
        "preregistered_utc": created_utc,
        "written_and_hashed_before_validation_replay": True,
        "data_regime": "REUSED_DEV_TEMPORAL_ROBUSTNESS_NOT_FRESH_OOS",
        "tested_code_sha": tested_code_sha,
        "canonical_dataset_sha256": dataset_sha256,
        "random_seed": int(random_seed),
        "h3_r2_excluded_from_executable_arms": True,
        "finite_search_budget": {
            "max_primary_arms": 3,
            "declared_primary_arms_count": len(hypotheses),
            "multiplicity_correction": "HOLM_BONFERRONI_FAMILY_M3_NOT_RELAXED",
            "discarded_variants_count": 0,
            "post_validation_threshold_tuning_permitted": False,
        },
        "hypotheses": hypotheses,
        "negative_controls": list(KH03_NEGATIVE_CONTROLS),
        "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2),
        "OOS_EVALUATIONS": 0,
        "ACTIVATION_READY": "NO",
        "status": "PASS",
    }
    plan["plan_frozen_sha256"] = canonical_hash({k: v for k, v in plan.items() if k != "plan_frozen_sha256"})
    return plan


def build_r3_train_only_candidates_asof(
    *,
    datasets: Mapping[str, Any],
    mtf_bundle: Mapping[str, Any],
    r2_cands_bundle: Mapping[str, Any],
    m5_ledger: Mapping[str, Any],
    costs: CostAssumptions,
    spread_usd: float = DEFAULT_MODELED_SPREAD_USD,
    seed: int = DEFAULT_RANDOM_SEED_KH02_R2,
) -> Dict[str, Any]:
    """
    Constructs E1, E2, E3, and negative-control candidate streams strictly as-of from the
    full R2 eligible opportunity stream (`NC_NULL_TRIGGER`), guaranteeing exact conservation
    (`accepted + rejected == base_eligible`) for every single arm!
    """
    bars_1m = list(datasets["1m"])
    rec_by_idx = _mtf_records_by_idx(mtf_bundle)
    base_null = sorted(
        list((r2_cands_bundle.get("arm_candidates") or {}).get("NC_NULL_TRIGGER") or ()),
        key=lambda c: (int(c["decision_timestamp_ms"]), str(c["direction"]), str(c["opportunity_id"])),
    )

    arm_cands: Dict[str, List[Dict[str, Any]]] = {ac: [] for ac in KH03_R3_ALL_ARMS}
    rej: Dict[str, Counter] = {ac: Counter() for ac in KH03_R3_ALL_ARMS}
    e1_by_day: Counter = Counter()
    null_by_day: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    e3_replaced_count = 0

    for c in base_null:
        idx = int(c["checkpoint_index_1m"])
        dec_ts = int(c["decision_timestamp_ms"])
        d = str(c["direction"]).upper()
        rec = rec_by_idx.get(idx)
        ny_d = str(c.get("ny_date") or (rec.ny_date if rec is not None else "UNK"))
        arm_cands["NC_NULL_TRIGGER"].append(c)
        null_by_day[ny_d].append(c)

        # E1: Low-friction stop floor + M15 trend alignment (pure filter)
        ent = float(c["entry_reference"])
        sl = float(c["selected_sl"])
        tp = float(c["take_profit"])
        sd = abs(ent - sl)
        atr_m1 = float(rec.m1_atr_14) if (rec is not None and rec.m1_atr_14 is not None) else 0.0
        m15_ok = (
            rec is not None
            and ((d == "LONG" and rec.m15_trend_dir == "BULL") or (d == "SHORT" and rec.m15_trend_dir == "BEAR"))
        )
        if sd >= E1_MIN_STOP_DIST_USD and atr_m1 > 0 and (sd / atr_m1) >= E1_MIN_STOP_TO_M1_ATR_RATIO and m15_ok:
            arm_cands["KH03_R3_E1_LOW_FRICTION_STOP_FLOOR"].append(c)
            e1_by_day[ny_d] += 1
        else:
            if sd < E1_MIN_STOP_DIST_USD:
                rej["KH03_R3_E1_LOW_FRICTION_STOP_FLOOR"]["STOP_DIST_LT_4p00_USD"] += 1
            elif not m15_ok:
                rej["KH03_R3_E1_LOW_FRICTION_STOP_FLOOR"]["FAILED_M15_TREND_ALIGN"] += 1
            else:
                rej["KH03_R3_E1_LOW_FRICTION_STOP_FLOOR"]["STOP_TO_M1_ATR_LT_1p25"] += 1

        # E2: Causal delayed closed-bar confirmation (1..6 bars, fill strictly on bar[idx+k+1].open!)
        b0_hi = float(bars_1m[idx]["high"])
        b0_lo = float(bars_1m[idx]["low"])
        confirmed_k: Optional[int] = None
        e2_rej_reason = "NO_BREAKOUT_CONFIRM_WITHIN_6_CLOSED_BARS"
        for k in range(1, E2_MAX_WAIT_CLOSED_BARS + 1):
            if idx + k + 1 >= len(bars_1m):
                e2_rej_reason = "INSUFFICIENT_BARS_AFTER_CONFIRM"
                break
            bk = bars_1m[idx + k]
            if d == "LONG":
                if float(bk["low"]) <= sl:
                    e2_rej_reason = "STOP_BREACHED_DURING_WAIT"
                    break
                if float(bk["close"]) >= tp:
                    e2_rej_reason = "TP_REACHED_DURING_WAIT"
                    break
                if float(bk["close"]) > b0_hi:
                    confirmed_k = k
                    break
            else:
                if float(bk["high"]) >= sl:
                    e2_rej_reason = "STOP_BREACHED_DURING_WAIT"
                    break
                if float(bk["close"]) <= tp:
                    e2_rej_reason = "TP_REACHED_DURING_WAIT"
                    break
                if float(bk["close"]) < b0_lo:
                    confirmed_k = k
                    break

        if confirmed_k is not None:
            new_idx = idx + confirmed_k
            new_dec_ts = int(bars_1m[new_idx]["close_time"])
            new_ent = float(bars_1m[new_idx]["close"])
            cr2 = calculate_risk_reward(
                direction=d,
                planned_entry=new_ent,
                stop_loss=sl,
                take_profit=tp,
                capital_usdt=1000.0,
                risk_pct=0.25,
                leverage=20,
                margin_mode="ISOLATED",
                min_net_rr=MIN_NET_RR_THRESHOLD,
                costs=costs,
                entry_has_slippage=False,
                now_ms=new_dec_ts,
            )
            if cr2.is_valid and cr2.can_execute and float(cr2.quantity) >= 0.01 - 1e-9 and float(cr2.net_risk_usdt) <= PLANNED_NET_RISK_CAP_USD + 1e-9 and float(cr2.net_rr) >= MIN_NET_RR_THRESHOLD - 1e-9:
                c2 = dict(c)
                c2["checkpoint_index_1m"] = new_idx
                c2["decision_timestamp_ms"] = new_dec_ts
                c2["base_decision_timestamp_ms"] = dec_ts
                c2["wait_closed_bars"] = confirmed_k
                c2["entry_reference"] = round(new_ent, 2)
                c2["hypothetical_quantity"] = round(float(cr2.quantity), 4)
                c2["planned_net_risk_usd"] = round(float(cr2.net_risk_usdt), 4)
                c2["net_rr"] = round(float(cr2.net_rr), 4)
                arm_cands["KH03_R3_E2_CAUSAL_DELAYED_CONFIRMATION"].append(c2)
            else:
                rej["KH03_R3_E2_CAUSAL_DELAYED_CONFIRMATION"]["FAILED_DELAYED_RERISK_RR_OR_CAP"] += 1
        else:
            rej["KH03_R3_E2_CAUSAL_DELAYED_CONFIRMATION"][e2_rej_reason] += 1

        # E3: Nearest causal M5 unswept structural target (exact same entry & SL!)
        q3 = query_nearest_causal_unswept_m5_target(
            m5_ledger=m5_ledger,
            direction=d,
            decision_idx_1m=idx,
            decision_ts_ms=dec_ts,
            entry_price=ent,
            selected_sl=sl,
            baseline_tp=tp,
            costs=costs,
        )
        c3 = dict(c)
        c3["take_profit"] = q3["selected_tp"]
        c3["r3_e3_target_source"] = q3["target_source"]
        c3["r3_e3_replaced_baseline_tp"] = bool(q3["replaced_baseline_tp"])
        if q3["replaced_baseline_tp"]:
            e3_replaced_count += 1
        arm_cands["KH03_R3_E3_CAUSAL_NEAREST_M5_UNSWEPT_EXIT"].append(c3)

        # NC_TIME_OFFSET_SHIFT: 15-bar stale M15 trend + stop floor check
        stale_rec = rec_by_idx.get(idx - 15)
        stale_m15_ok = (
            stale_rec is not None
            and ((d == "LONG" and stale_rec.m15_trend_dir == "BULL") or (d == "SHORT" and stale_rec.m15_trend_dir == "BEAR"))
        )
        if sd >= E1_MIN_STOP_DIST_USD and stale_m15_ok:
            arm_cands["NC_TIME_OFFSET_SHIFT"].append(c)
        else:
            rej["NC_TIME_OFFSET_SHIFT"]["FAILED_STALE_15M_SHIFT_CONTROL"] += 1

    # NC_PLACEBO_SHUFFLED_TRIGGER: deterministic within-NY-day density match to E1
    rng = random.Random(seed + 409)
    for ny_d in sorted(null_by_day.keys()):
        day_pool = list(null_by_day[ny_d])
        k_pick = min(len(day_pool), int(e1_by_day.get(ny_d, 0)))
        idxs = list(range(len(day_pool)))
        rng.shuffle(idxs)
        picked = set(idxs[:k_pick])
        for i, c in enumerate(day_pool):
            if i in picked:
                arm_cands["NC_PLACEBO_SHUFFLED_TRIGGER"].append(c)
            else:
                rej["NC_PLACEBO_SHUFFLED_TRIGGER"]["NOT_SELECTED_BY_PLACEBO_SHUFFLE"] += 1

    for ac in KH03_R3_ALL_ARMS:
        arm_cands[ac].sort(key=lambda x: (int(x["decision_timestamp_ms"]), str(x["direction"]), str(x["opportunity_id"])))

    base_n = len(base_null)
    arm_cons: Dict[str, Any] = {}
    all_cons = True
    for ac in KH03_R3_ALL_ARMS:
        acc_n = len(arm_cands[ac])
        rej_n = sum(rej[ac].values())
        ok_c = (acc_n + rej_n == base_n)
        if not ok_c:
            all_cons = False
        arm_cons[ac] = {
            "base_eligible_denominator": base_n,
            "accepted_count": acc_n,
            "rejected_count": rej_n,
            "rejection_breakdown": dict(sorted(rej[ac].items())),
            "conservation_exact": ok_c,
            "candidate_ids_sha256": canonical_hash([x["opportunity_id"] for x in arm_cands[ac]]),
        }

    return {
        "base_eligible_count": base_n,
        "e3_replaced_baseline_tp_with_nearer_m5_count": e3_replaced_count,
        "all_arms_conserved_exact": all_cons,
        "arm_candidates": arm_cands,
        "arm_conservation": arm_cons,
    }


def run_r3_forward_only_dev_evaluation(
    *,
    datasets: Mapping[str, Any],
    mtf_bundle: Mapping[str, Any],
    r3_cands_1x: Mapping[str, Any],
    prereg_plan_11: Mapping[str, Any],
    tested_code_sha: str,
    seed: int = DEFAULT_RANDOM_SEED_KH02_R2,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Produces Artifacts 13 and 14 using KH-03.R2's strict expanding forward-only DEV folds:
      - `13_r2_forward_only_fold_boundaries_freeze_no_peek.json`
      - `14_validation_fold_independent_results_by_arm.json`
    """
    bars_1m = list(datasets["1m"])
    rec_by_idx = _mtf_records_by_idx(mtf_bundle)
    fold_spec = build_kh03_r2_forward_only_dev_folds(
        bars_1m=bars_1m,
        mtf_records_by_idx=rec_by_idx,
        embargo_minutes=MIN_EMBARGO_MINUTES_KH03_R2,
        max_label_horizon_minutes=MAX_SIM_BAR_HORIZON_MINUTES_KH03_R2,
        n_blocks=4,
    )
    folds_raw = list(fold_spec["folds"])

    enriched_folds: List[Dict[str, Any]] = []
    fold_replays: List[Dict[str, Any]] = []
    arm_fold_exps: Dict[str, List[float]] = {ac: [] for ac in KH03_R3_ALL_ARMS}
    arm_stitched_eps: Dict[str, List[Dict[str, Any]]] = {ac: [] for ac in KH03_R3_ALL_ARMS}
    total_ov = 0

    for f in folds_raw:
        fid = str(f["fold_id"])
        tr_s_i, tr_e_i = int(f["train_start_idx"]), int(f["train_end_idx"])
        val_s_i, val_e_i = int(f["val_start_idx"]), int(f["val_end_idx"])
        emb_s_ms = int(f["embargo_start_ms"])

        train_sums: Dict[str, Dict[str, Any]] = {}
        train_ep_ids: List[str] = []
        train_last_exit: Optional[int] = None
        purged_n = 0

        for ac in KH03_R3_PRIMARY_ARMS:
            tsum, teps, tpurged = _replay_chronological_window_bounded(
                arm_code=ac, window_id=f"{fid}_TRAIN",
                candidates=r3_cands_1x["arm_candidates"][ac], bars_1m=bars_1m,
                window_start_idx=tr_s_i, window_end_idx=tr_e_i, hard_horizon_cutoff_ms=emb_s_ms,
            )
            total_ov += int(tsum.get("overlap_violations", 0))
            train_sums[ac] = tsum
            purged_n += len(tpurged)
            for te in teps:
                train_ep_ids.append(f"{ac}:{te['opportunity_id']}")
                lex = int(te["label_exit_ts_ms"])
                if train_last_exit is None or lex > train_last_exit:
                    train_last_exit = lex

        sel_freeze = select_past_only_arm_for_fold(
            fold_id=fid,
            tested_code_sha=tested_code_sha,
            train_arm_summaries=train_sums,
            candidate_arms=KH03_R3_PRIMARY_ARMS,
        )

        val_results: Dict[str, Any] = {}
        val_ep_ids: List[str] = []
        for ac in KH03_R3_ALL_ARMS:
            vsum, veps, _ = _replay_chronological_window_bounded(
                arm_code=ac, window_id=f"{fid}_VAL",
                candidates=r3_cands_1x["arm_candidates"][ac], bars_1m=bars_1m,
                window_start_idx=val_s_i, window_end_idx=val_e_i,
                hard_horizon_cutoff_ms=None, censor_label_at_window_end="OPEN_AT_FOLD_END",
            )
            total_ov += int(vsum.get("overlap_violations", 0))
            if vsum.get("expectancy_r") is not None and int(vsum.get("closed") or 0) > 0:
                arm_fold_exps[ac].append(float(vsum["expectancy_r"]))
            arm_stitched_eps[ac].extend(veps)
            for ve in veps:
                val_ep_ids.append(f"{ac}:{ve['opportunity_id']}")
            val_results[ac] = {
                "summary": vsum,
                "fold_sample_status": "INCONCLUSIVE_LOW_FOLD_N" if int(vsum.get("closed") or 0) < 15 else "EVALUATED_REUSED_DEV_FOLD",
            }

        f_enr = dict(f)
        f_enr["train_last_label_exit_ts_ms"] = train_last_exit
        f_enr["train_episode_ids"] = sorted(train_ep_ids)
        f_enr["val_episode_ids"] = sorted(val_ep_ids)
        f_enr["purged_train_boundary_trades_count"] = purged_n
        enriched_folds.append(f_enr)
        fold_replays.append(
            {
                "fold_id": fid,
                "fold_local_selection_freeze": sel_freeze,
                "selected_arm_validation_result": val_results[sel_freeze["selected_arm_on_past_train_only"]],
                "all_arms_validation_results": val_results,
            }
        )

    caus_check = validate_kh03_r2_fold_causality(enriched_folds, min_embargo_minutes=MIN_EMBARGO_MINUTES_KH03_R2)

    # Future-win injection invariance proof on Fold 1
    future_inj_ok = False
    if folds_raw:
        f1 = folds_raw[0]
        fake_cands = list(r3_cands_1x["arm_candidates"]["KH03_R3_E1_LOW_FRICTION_STOP_FLOOR"])
        fake_cands.append(
            {
                "opportunity_id": "FAKE_FUTURE_R3_HUGE_WIN",
                "checkpoint_index_1m": int(f1["val_start_idx"]) + 40,
                "decision_timestamp_ms": int(bars_1m[int(f1["val_start_idx"]) + 40]["close_time"]),
                "direction": "LONG", "entry_reference": 4100.0, "selected_sl": 4095.0, "take_profit": 4200.0, "ny_date": "2026-10-20",
            }
        )
        t_orig, _, _ = _replay_chronological_window_bounded(
            arm_code="KH03_R3_E1_LOW_FRICTION_STOP_FLOOR", window_id="FINJ_O",
            candidates=r3_cands_1x["arm_candidates"]["KH03_R3_E1_LOW_FRICTION_STOP_FLOOR"],
            bars_1m=bars_1m, window_start_idx=int(f1["train_start_idx"]), window_end_idx=int(f1["train_end_idx"]),
            hard_horizon_cutoff_ms=int(f1["embargo_start_ms"]),
        )
        t_mut, _, _ = _replay_chronological_window_bounded(
            arm_code="KH03_R3_E1_LOW_FRICTION_STOP_FLOOR", window_id="FINJ_M",
            candidates=fake_cands, bars_1m=bars_1m,
            window_start_idx=int(f1["train_start_idx"]), window_end_idx=int(f1["train_end_idx"]),
            hard_horizon_cutoff_ms=int(f1["embargo_start_ms"]),
        )
        future_inj_ok = (t_orig["executed_ids_sha256"] == t_mut["executed_ids_sha256"])

    folds_compact = []
    for fe in enriched_folds:
        fc = {k: v for k, v in fe.items() if k not in ("train_episode_ids", "val_episode_ids")}
        fc["train_episodes_count"] = len(fe["train_episode_ids"])
        fc["val_episodes_count"] = len(fe["val_episode_ids"])
        folds_compact.append(fc)

    f13: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH03_R3,
        "schema_version": SCHEMA_VERSION_KH03_R3,
        "preregistered_plan_sha256": prereg_plan_11["plan_frozen_sha256"],
        "temporal_validation_kind": "FORWARD_ONLY_DEV_VALIDATION",
        "is_fresh_oos": False,
        "OOS_EVALUATIONS": 0,
        "embargo_minutes": int(fold_spec["embargo_minutes"]),
        "valid_folds_count": len(folds_compact),
        "future_win_injection_invariance_passed": future_inj_ok,
        "folds": folds_compact,
        "causality_audit": caus_check,
        "status": "PASS" if (caus_check["valid"] and future_inj_ok and total_ov == 0) else "FAIL",
    }
    f13["artifact_canonical_sha256"] = canonical_hash({k: v for k, v in f13.items() if k != "artifact_canonical_sha256"})

    stitched_by_arm: Dict[str, Any] = {}
    for ac in KH03_R3_ALL_ARMS:
        eps_all = arm_stitched_eps[ac]
        dup_n = len(eps_all) - len({e["opportunity_id"] for e in eps_all})
        exps = sorted(arm_fold_exps[ac])
        stitched_by_arm[ac] = {
            "duplicate_opportunity_ids_across_folds": dup_n,
            "stitched_summary": _summarize_episodes(eps_all),
            "median_fold_expectancy_r": exps[len(exps) // 2] if exps else None,
            "positive_fold_ratio": round(sum(1 for x in exps if x > 0.0) / max(1, len(exps)), 4) if exps else 0.0,
            "per_fold_expectancy_r": arm_fold_exps[ac],
        }

    f14: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH03_R3,
        "schema_version": SCHEMA_VERSION_KH03_R3,
        "preregistered_plan_sha256": prereg_plan_11["plan_frozen_sha256"],
        "temporal_validation_kind": "FORWARD_ONLY_DEV_VALIDATION",
        "is_fresh_oos": False,
        "total_overlap_violations": total_ov,
        "per_fold_selection_and_validation_replays": fold_replays,
        "stitched_non_overlapping_validation_by_arm": stitched_by_arm,
        "status": "PASS" if (caus_check["valid"] and total_ov == 0 and all(v["duplicate_opportunity_ids_across_folds"] == 0 for v in stitched_by_arm.values())) else "FAIL",
    }
    f14["artifact_canonical_sha256"] = canonical_hash({k: v for k, v in f14.items() if k != "artifact_canonical_sha256"})
    return f13, f14


def run_r3_paired_and_strict_dev_evaluation_15_16(
    *,
    datasets: Mapping[str, Any],
    mtf_bundle: Mapping[str, Any],
    ledger_bundle: Mapping[str, Any],
    m5_ledger: Mapping[str, Any],
    r2_cands_1x: Mapping[str, Any],
    r3_cands_1x: Mapping[str, Any],
    prereg_plan_11: Mapping[str, Any],
    wf_13: Mapping[str, Any],
    wf_14: Mapping[str, Any],
    seed: int = DEFAULT_RANDOM_SEED_KH02_R2,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Produces Artifacts 15 and 16:
      - `15_fixed_fill_vs_dynamic_rerisk_and_expense_truth.json`: all 6 cost scenarios under
        FULL_DYNAMIC_RERISK plus separate FIXED_FILLS descriptive repricing for E1/E2/E3/controls,
        plus paired same-opportunity comparison of E3 vs NULL.
      - `16_strict_dev_gate_power_holm_cluster_bootstrap.json`: strict R2 CI + DEV advancement
        recomputation (`evaluate_kh03_dev_ci_and_advancement`), Holm-Bonferroni m=3, matching,
        KH-04 power grid, synthetic positive control proof, and honest economic verdicts!
    """
    bars_1m = list(datasets["1m"])
    rec_by_idx = _mtf_records_by_idx(mtf_bundle)

    # Full 1X replays
    base_reps_1x: Dict[str, Dict[str, Any]] = {}
    base_eps_1x: Dict[str, List[Dict[str, Any]]] = {}
    for ac in KH03_R3_ALL_ARMS:
        rs, eps = _replay_chronological_custom_cost_scenario(
            arm_code=ac, scenario_id="1X_BASE_0p0004",
            candidates=r3_cands_1x["arm_candidates"][ac], bars_1m=bars_1m,
            spread_usd=0.20, slippage_usd=0.10, taker_fee_rate=0.0004, maker_fee_rate=0.0002,
            return_episodes=True,
        )
        base_reps_1x[ac] = rs
        base_eps_1x[ac] = eps

    # 6-scenario FULL_DYNAMIC_RERISK across E1/E2/E3/controls
    dyn_scenarios: Dict[str, Any] = {}
    total_ov = 0
    zero_fee_bugs = 0
    for sc_id, sp_usd, slp_usd, taker_r, maker_r in KH03_SIX_COST_SCENARIOS:
        if sc_id == "1X_BASE_0p0004":
            sc_cands = r3_cands_1x
            sc_arms = {ac: dict(base_reps_1x[ac]) for ac in KH03_R3_ALL_ARMS}
        else:
            costs = CostAssumptions(maker_fee_rate=maker_r, taker_fee_rate=taker_r, slippage_usd=slp_usd)
            r2_sc = build_r2_target_repair_full_opportunity_conservation(
                datasets=datasets, mtf_bundle=mtf_bundle, ledger_bundle=ledger_bundle, r1_candidates=None, cost_assumptions=costs
            )
            sc_cands = build_r3_train_only_candidates_asof(
                datasets=datasets, mtf_bundle=mtf_bundle, r2_cands_bundle=r2_sc, m5_ledger=m5_ledger, costs=costs, spread_usd=sp_usd, seed=seed
            )
            sc_arms = {}
            for ac in KH03_R3_ALL_ARMS:
                rs = _replay_chronological_custom_cost_scenario(
                    arm_code=ac, scenario_id=sc_id,
                    candidates=sc_cands["arm_candidates"][ac], bars_1m=bars_1m,
                    spread_usd=sp_usd, slippage_usd=slp_usd, taker_fee_rate=taker_r, maker_fee_rate=maker_r,
                )
                total_ov += int(rs.get("overlap_violations", 0))
                sc_arms[ac] = rs
        for ac, rs in sc_arms.items():
            g_d = Decimal(str(rs.get("gross_pnl_usd") or 0.0))
            f_d = Decimal(str(rs.get("total_fees_usd") or 0.0))
            n_d = Decimal(str(rs.get("net_pnl_usd") or 0.0))
            if int(rs.get("closed") or 0) > 0 and f_d <= Decimal("0") and (g_d - n_d) > Decimal("0.01"):
                zero_fee_bugs += 1
        dyn_scenarios[sc_id] = {
            "parameters": {"spread_usd": sp_usd, "slippage_usd": slp_usd, "taker_fee_rate": taker_r, "maker_fee_rate": maker_r},
            "population_mode": "FULL_DYNAMIC_RERISK",
            "base_eligible_denominator": sc_cands["base_eligible_count"],
            "arms": sc_arms,
        }

    # Separate FIXED_FILLS descriptive repricing on 1X closed fills (clearly segregated from dynamic re-risk!)
    fixed_fills_desc: Dict[str, Any] = {}
    for ac in KH03_R3_PRIMARY_ARMS:
        cl_1x = [e for e in base_eps_1x[ac] if e["filled"] and e["outcome_label"] in ("WIN", "LOSS")]
        g_sum = sum((Decimal(str(e["realized_gross_pnl_usd"])) for e in cl_1x), Decimal("0"))
        f_sum = sum((Decimal(str(e["total_fees_usd"])) for e in cl_1x), Decimal("0"))
        fixed_fills_desc[ac] = {
            "mode": "FIXED_FILLS_DESCRIPTIVE_ONLY_NOT_DYNAMIC_RERISK",
            "fixed_1x_closed_count": len(cl_1x),
            "gross_pnl_usd": _dec4(g_sum),
            "fee_1x_0p0004_usd": _dec4(f_sum),
            "net_1x_usd": _dec4(g_sum - f_sum),
            "net_fee_scaled_1p5x_0p0006_usd": _dec4(g_sum - f_sum * Decimal("1.5")),
            "net_fee_scaled_2x_0p0008_usd": _dec4(g_sum - f_sum * Decimal("2.0")),
        }

    # Paired same-opportunity comparison for E3 vs NULL (identical opportunity IDs!)
    null_by_oid = {e["opportunity_id"]: e for e in base_eps_1x["NC_NULL_TRIGGER"] if e["filled"] and e["outcome_label"] in ("WIN", "LOSS")}
    e3_by_oid = {e["opportunity_id"]: e for e in base_eps_1x["KH03_R3_E3_CAUSAL_NEAREST_M5_UNSWEPT_EXIT"] if e["filled"] and e["outcome_label"] in ("WIN", "LOSS")}
    common_oids = sorted(set(null_by_oid.keys()) & set(e3_by_oid.keys()))
    e3_paired_deltas = [round(float(e3_by_oid[o]["realized_net_r"]) - float(null_by_oid[o]["realized_net_r"]), 4) for o in common_oids]

    f15: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH03_R3,
        "schema_version": SCHEMA_VERSION_KH03_R3,
        "quote_mode": "MODELED_BID_ASK",
        "observed_l1_bbo_available": False,
        "ACCOUNT_EFFECTIVE_FEE_VERDICT": "UNKNOWN_NOT_LOCALLY_VERIFIED",
        "total_overlap_violations": total_ov,
        "zero_fee_display_bugs_count": zero_fee_bugs,
        "dynamic_full_rerisk_six_scenarios": dyn_scenarios,
        "fixed_fills_descriptive_repricing": fixed_fills_desc,
        "paired_same_opportunity_e3_vs_null": {
            "common_closed_opportunity_count": len(common_oids),
            "mean_delta_r_e3_minus_null": round(sum(e3_paired_deltas) / len(e3_paired_deltas), 4) if e3_paired_deltas else None,
            "e3_improved_count": sum(1 for x in e3_paired_deltas if x > 1e-6),
            "e3_worsened_count": sum(1 for x in e3_paired_deltas if x < -1e-6),
            "e3_unchanged_count": sum(1 for x in e3_paired_deltas if abs(x) <= 1e-6),
        },
        "status": "PASS" if (total_ov == 0 and zero_fee_bugs == 0) else "FAIL",
    }
    f15["artifact_canonical_sha256"] = canonical_hash({k: v for k, v in f15.items() if k != "artifact_canonical_sha256"})

    # 16: Strict R2 CI + DEV advancement gate + Holm-Bonferroni m=3 + matching + power grid + synthetic positive proof
    m1_mid_ts = int(bars_1m[len(bars_1m) // 2]["close_time"]) if bars_1m else 0
    null_closed = [e for e in base_eps_1x["NC_NULL_TRIGGER"] if e["filled"] and e["outcome_label"] in ("WIN", "LOSS")]

    def _covs(ep: Mapping[str, Any]) -> Dict[str, Any]:
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
        return {"is_long": is_long, "is_liquid_session": is_liq, "h1_atr_high_vol": hi_vol, "time_second_half": half2, "stratum_key": f"{int(is_long)}|{int(is_liq)}|{int(hi_vol)}|{int(half2)}"}

    cov_names = ("is_long", "is_liquid_session", "h1_atr_high_vol", "time_second_half")
    null_by_s: Dict[str, List[Tuple[Dict[str, Any], Dict[str, Any]]]] = defaultdict(list)
    for ep in null_closed:
        cv = _covs(ep)
        null_by_s[cv["stratum_key"]].append((ep, cv))

    matching_16: Dict[str, Any] = {}
    raw_pvals: Dict[str, Optional[float]] = {}
    arm_metrics: Dict[str, Dict[str, Any]] = {}
    stitched_wf = wf_14.get("stitched_non_overlapping_validation_by_arm") or {}
    valid_wf_folds_n = int(wf_13.get("valid_folds_count") or 0)

    for ac in KH03_R3_PRIMARY_ARMS:
        cl_eps = [e for e in base_eps_1x[ac] if e["filled"] and e["outcome_label"] in ("WIN", "LOSS")]
        t_by_s: Dict[str, List[Tuple[Dict[str, Any], Dict[str, Any]]]] = defaultdict(list)
        for ep in cl_eps:
            cv = _covs(ep)
            t_by_s[cv["stratum_key"]].append((ep, cv))
        m_t, m_c, deltas = [], [], []
        for sk in sorted(set(t_by_s.keys()) & set(null_by_s.keys())):
            t_it = sorted(t_by_s[sk], key=lambda x: (int(x[0]["decision_timestamp_ms"]), str(x[0]["opportunity_id"])))
            c_it = sorted(null_by_s[sk], key=lambda x: (int(x[0]["decision_timestamp_ms"]), str(x[0]["opportunity_id"])))
            used: Set[int] = set()
            for te, tcv in t_it:
                best_j, best_dt = None, None
                for j, (ce, _) in enumerate(c_it):
                    if j in used:
                        continue
                    dt = abs(int(ce["decision_timestamp_ms"]) - int(te["decision_timestamp_ms"]))
                    if best_dt is None or dt < best_dt:
                        best_dt, best_j = dt, j
                if best_j is not None:
                    used.add(best_j)
                    ce, ccv = c_it[best_j]
                    m_t.append(tcv)
                    m_c.append(ccv)
                    deltas.append(round(float(te["realized_net_r"]) - float(ce["realized_net_r"]), 6))
        post_smds = {cn: _compute_smd([r[cn] for r in m_t], [r[cn] for r in m_c]) for cn in cov_names}
        est_p = [abs(v) for v in post_smds.values() if v is not None]
        max_p = round(max(est_p), 4) if est_p else None
        cov_r = round(len(m_t) / max(1, len(cl_eps)), 4) if cl_eps else 0.0
        mq = "BALANCED_COMMON_SUPPORT" if (len(m_t) >= 20 and cov_r >= 0.70 and est_p and all(v <= 0.10 + 1e-9 for v in est_p)) else "MATCHING_INADEQUATE"
        matching_16[ac] = {
            "matched_pairs_count": len(m_t),
            "overlap_coverage_ratio": cov_r,
            "max_post_abs_smd": max_p,
            "matching_quality_status": mq,
            "matched_mean_delta_r_vs_null": round(sum(deltas) / len(deltas), 4) if deltas else None,
            "expectancy_r_vs_placebo_shuffled": (
                round(float(base_reps_1x[ac]["expectancy_r"]) - float(base_reps_1x["NC_PLACEBO_SHUFFLED_TRIGGER"]["expectancy_r"]), 4)
                if (base_reps_1x[ac].get("expectancy_r") is not None and base_reps_1x["NC_PLACEBO_SHUFFLED_TRIGGER"].get("expectancy_r") is not None)
                else None
            ),
            "expectancy_r_vs_time_offset": (
                round(float(base_reps_1x[ac]["expectancy_r"]) - float(base_reps_1x["NC_TIME_OFFSET_SHIFT"]["expectancy_r"]), 4)
                if (base_reps_1x[ac].get("expectancy_r") is not None and base_reps_1x["NC_TIME_OFFSET_SHIFT"].get("expectancy_r") is not None)
                else None
            ),
        }

        r_vals = [float(e["realized_net_r"]) for e in cl_eps]
        ny_days = [str(e.get("ny_date") or "UNK") for e in cl_eps]
        ep_ids = [str(e.get("setup_episode_id") or e["opportunity_id"]) for e in cl_eps]
        raw_pvals[ac] = _one_sided_cluster_bootstrap_pvalue_gt_zero(r_vals, ny_days, seed=seed + 801 + KH03_R3_PRIMARY_ARMS.index(ac))
        wf_ac = stitched_wf.get(ac) or {}
        arm_metrics[ac] = {
            "closed_trades_1x": len(cl_eps),
            "wins_1x": sum(1 for e in cl_eps if e["outcome_label"] == "WIN"),
            "losses_1x": sum(1 for e in cl_eps if e["outcome_label"] == "LOSS"),
            "win_rate_1x": round(sum(1 for e in cl_eps if e["outcome_label"] == "WIN") / max(1, len(cl_eps)), 4) if cl_eps else 0.0,
            "effective_ny_days_1x": len(set(ny_days)),
            "expectancy_r_1x": base_reps_1x[ac].get("expectancy_r"),
            "net_pnl_usd_1x": base_reps_1x[ac].get("net_pnl_usd"),
            "total_fees_usd_1x": base_reps_1x[ac].get("total_fees_usd"),
            "expectancy_r_fee_only_0p0006": dyn_scenarios["FEE_ONLY_0p0006"]["arms"][ac].get("expectancy_r"),
            "expectancy_r_2x": dyn_scenarios["2X_ALL_COST_0p0004"]["arms"][ac].get("expectancy_r"),
            "expectancy_r_3x": dyn_scenarios["3X_ALL_COST_0p0004"]["arms"][ac].get("expectancy_r"),
            "ny_day_cluster_95ci_r": _block_bootstrap_ci(r_vals, ny_days, seed=seed + 31) if r_vals else None,
            "episode_cluster_95ci_r": _block_bootstrap_ci(r_vals, ep_ids, seed=seed + 37) if r_vals else None,
            "raw_one_sided_pvalue_gt_zero": raw_pvals[ac],
            "forward_only_wf_median_fold_expectancy_r": wf_ac.get("median_fold_expectancy_r"),
            "forward_only_wf_positive_fold_ratio": wf_ac.get("positive_fold_ratio"),
            "forward_only_wf_valid_folds_count": valid_wf_folds_n,
            "fidelity_tier": "MODELED_RESEARCH_COUNTERFACTUAL",
        }

    holm_pvals = _holm_bonferroni_adjust(raw_pvals)
    evals_16: Dict[str, Any] = {}
    passing_dev: List[str] = []
    for ac in KH03_R3_PRIMARY_ARMS:
        m = arm_metrics[ac]
        m["holm_bonferroni_adjusted_pvalue"] = holm_pvals[ac]
        mr = matching_16[ac]
        ev = evaluate_kh03_dev_ci_and_advancement(
            arm_code=ac,
            closed_trades_1x=m["closed_trades_1x"],
            effective_ny_days_1x=m["effective_ny_days_1x"],
            expectancy_r_1x=m["expectancy_r_1x"],
            net_pnl_usd_1x=m["net_pnl_usd_1x"],
            expectancy_r_fee_only_0p0006=m["expectancy_r_fee_only_0p0006"],
            expectancy_r_2x=m["expectancy_r_2x"],
            raw_ny_day_95ci_r=m["ny_day_cluster_95ci_r"],
            holm_bonferroni_adjusted_pvalue=holm_pvals[ac],
            matching_quality_status=mr["matching_quality_status"],
            matched_mean_delta_r_vs_null=mr["matched_mean_delta_r_vs_null"],
            expectancy_r_vs_placebo_shuffled=mr["expectancy_r_vs_placebo_shuffled"],
            expectancy_r_vs_time_offset=mr["expectancy_r_vs_time_offset"],
            wf_median_fold_expectancy_r=m["forward_only_wf_median_fold_expectancy_r"],
            wf_positive_fold_ratio=m["forward_only_wf_positive_fold_ratio"],
            wf_valid_forward_only_folds_count=valid_wf_folds_n,
            executable_edge_eligible=True,
        )
        m["ci_parsed"] = ev["ci_parsed"]
        m["ci_schema_valid"] = ev["ci_schema_valid"]
        m["subgates"] = ev["subgates"]
        m["failed_advancement_criteria"] = ev["failed_advancement_criteria"]
        m["passes_preregistered_dev_advancement"] = ev["passes_preregistered_dev_advancement"]
        m["arm_dev_verdict"] = ev["arm_dev_verdict"]
        evals_16[ac] = m
        if ev["passes_preregistered_dev_advancement"]:
            passing_dev.append(ac)

    # Synthetic positive control proving the strict gate CAN pass when ALL criteria truly hold
    synth_pos_ctrl = evaluate_kh03_dev_ci_and_advancement(
        arm_code="SYNTH_POSITIVE_GATE_CAPABILITY_CHECK",
        closed_trades_1x=90, effective_ny_days_1x=15,
        expectancy_r_1x=0.35, net_pnl_usd_1x=78.0,
        expectancy_r_fee_only_0p0006=0.25, expectancy_r_2x=0.15,
        raw_ny_day_95ci_r=[0.12, 0.58], holm_bonferroni_adjusted_pvalue=0.01,
        matching_quality_status="BALANCED_COMMON_SUPPORT", matched_mean_delta_r_vs_null=0.30,
        expectancy_r_vs_placebo_shuffled=0.32, expectancy_r_vs_time_offset=0.28,
        wf_median_fold_expectancy_r=0.22, wf_positive_fold_ratio=1.0, wf_valid_forward_only_folds_count=3,
        executable_edge_eligible=True,
    )

    all_ci_ok = all(bool(v["ci_schema_valid"]) for v in evals_16.values())
    dev_verdict = "DEV_CANDIDATE_UNPROVEN_OOS" if passing_dev else "NO_ECONOMIC_ADVANCEMENT_CANDIDATE"
    kh04_verdict = "BLOCKED_NO_ELIGIBLE_PROSPECTIVE_DATA" if passing_dev else "BLOCKED_NO_DEV_CANDIDATE"

    f16: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH03_R3,
        "schema_version": SCHEMA_VERSION_KH03_R3,
        "preregistered_plan_sha256": prereg_plan_11["plan_frozen_sha256"],
        "preregistered_hypothesis_budget_m": 3,
        "multiplicity_correction": "HOLM_BONFERRONI_FAMILY_M3_NOT_RELAXED",
        "primary_arm_evaluations": evals_16,
        "matching_reports": matching_16,
        "negative_control_summaries_1x": {nc: base_reps_1x[nc] for nc in KH03_NEGATIVE_CONTROLS},
        "synthetic_positive_gate_capability_proof": synth_pos_ctrl,
        "kh04_prospective_oos_power_grid": compute_prospective_oos_power_requirements_r2_c1(num_primary_arms=3),
        "passing_dev_candidate_arms": passing_dev,
        "DEV_RESEARCH_CANDIDATE": dev_verdict,
        "RESEARCH_ECONOMIC_VERDICT": "NO_PROVEN_EDGE",
        "FRESH_OOS": "BLOCKED_NO_UNTOUCHED_DATA",
        "KH04_ENTRY": kh04_verdict,
        "OOS_EVALUATIONS": 0,
        "ACTIVATION_READY": "NO",
        "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2),
        "all_ci_schemas_valid": all_ci_ok,
        "status": "PASS" if (all_ci_ok and synth_pos_ctrl["passes_preregistered_dev_advancement"]) else "FAIL",
    }
    f16["artifact_canonical_sha256"] = canonical_hash({k: v for k, v in f16.items() if k != "artifact_canonical_sha256"})
    return f15, f16
