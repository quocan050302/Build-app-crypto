"""
aurum-desk/backend/services/kh_03_r2_methodology_service.py

AURUM DESK — KH-03.R2 | RESEARCH METHODOLOGY CLOSURE
SPEC VERSION: KH-03.R2-v1.0 | RESEARCH ONLY | OOS_EVALUATIONS=0 | ACTIVATION_READY=NO

Closes the three verified KH-03.R1 research methodology defects WITHOUT altering any locked
production engine, authoritative simulator, cost defaults, or sealed R1/C4 artifacts:
  - P0-SCI-01: Strict 95% cluster-CI schema adapter (`parse_kh03_cluster_ci_strict`) and
               independent raw-evidence DEV advancement evaluator (`evaluate_kh03_dev_ci_and_advancement`).
  - P0-SCI-02: Truthful H3 fill-time policy classification (PATH B `H3_FILL_TIME_FILTER_ONLY_RESEARCH_ADVISORY`
               when only M1 modeled OHLCV is available with `observed_l1_bbo_available=False`),
               explicitly rejecting `OPEN_TO_OPEN` instantaneous fill-after-observation, preserving
               legacy R1 filter-only replay strictly for descriptive compatibility, and emitting
               streaming Decimal advisory ledger `08_h3_per_episode_policy_qty_time_ledger.jsonl.gz`.
  - P0-SCI-03: Strictly chronological expanding forward-only DEV temporal folds
               (`build_kh03_r2_forward_only_dev_folds`, `validate_kh03_r2_fold_causality`,
               `run_kh03_r2_forward_only_walkforward`) with past-only train (`train_end < val_start`),
               embargo `>= 240 min` + exact per-episode label-exit / horizon purging, fold-local
               selection freeze BEFORE validation, and fresh fold-local 1-position validation replay
               with hard fold-end horizon truncation (never slicing a full-sample replay post-hoc).
"""
from __future__ import annotations

from collections import Counter, defaultdict
import copy
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import gzip
import hashlib
import json
import math
from pathlib import Path
import random
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from domain_calculator import CostAssumptions
from services.kh_02_entry_foundation_research_service import (
    KH02AsOfMTFRecord,
    build_kh02_asof_mtf_features,
)
from services.kh_02_r2_prospective_data_service import (
    compute_prospective_oos_power_requirements_r2_c1,
    normalize_ny_local_to_utc_ms_r2_c1,
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
    evaluate_causal_fill_time_policy_single_trade,
)
from services.kh_03_fee_integrity_service import (
    extract_authoritative_ledger_fees_kh03,
)
from services.kh_03_research_service import (
    KH03_ALL_ARMS,
    KH03_NEGATIVE_CONTROLS,
    KH03_PRIMARY_ARMS,
    KH03_SIX_COST_SCENARIOS,
    _holm_bonferroni_adjust,
    _mtf_records_by_idx,
    _nearest_rank_quantile,
    _one_sided_cluster_bootstrap_pvalue_gt_zero,
    build_kh03_arm_candidates_from_r2_universe,
)
from services.v17_9_2_structural_stop_intelligence import simulate_v17931_trade_execution_net
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash

SPEC_VERSION_KH03_R2 = "KH-03.R2-v1.0"
SCHEMA_VERSION_KH03_R2 = "kh_03_r2_methodology_closure_v1"

MIN_EMBARGO_MINUTES_KH03_R2 = 240
MIN_EMBARGO_MS_KH03_R2 = MIN_EMBARGO_MINUTES_KH03_R2 * 60_000
MAX_SIM_BAR_HORIZON_MINUTES_KH03_R2 = 180

# Exact executable primary arms under PATH B (H3 disqualified as filter-only research advisory!)
KH03_R2_EXECUTABLE_PRIMARY_ARMS: Tuple[str, ...] = (
    "KH03_H1_MTF_UNSWEPT_ALIGN",
    "KH03_H2_LOW_FRICTION_VOL_REGIME",
)
KH03_R2_H3_ADVISORY_ARM = "KH03_H3_FILL_TIME_CAUSAL_RR_GATE"

VALID_CI_STATUSES_KH03_R2: Tuple[str, ...] = (
    "CI_VALID_POSITIVE",
    "CI_VALID_NEGATIVE",
    "CI_STRADDLES_ZERO",
    "CI_TOUCHES_ZERO",
    "CI_UNAVAILABLE_INSUFFICIENT_SAMPLE",
    "CI_INVALID_SCHEMA",
)


def _dec4(v: Decimal) -> float:
    return float(v.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_strict_finite_number(x: Any) -> bool:
    if isinstance(x, bool):
        return False
    if not isinstance(x, (int, float)):
        return False
    return math.isfinite(float(x))


# ==============================================================================
# SECTION A — P0-SCI-01: STRICT CI PARSER & RAW-EVIDENCE DEV ADVANCEMENT GATE
# ==============================================================================


def parse_kh03_cluster_ci_strict(
    value: Any,
    *,
    label: str = "ny_day_cluster_95ci_r",
) -> Dict[str, Any]:
    """
    Strictly parses a cluster bootstrap 95% CI payload without silent truthiness bugs.
    Accepted inputs:
      - `None` -> `CI_UNAVAILABLE_INSUFFICIENT_SAMPLE` (`available=False`, `schema_valid=True`)
      - Canonical 2-element list/tuple `[lower, upper]` of strict finite ints/floats with `lower <= upper`
      - Optional strict exact 2-key mapping `{"ci_low": lo, "ci_high": hi}` of strict finite ints/floats
        with `lo <= hi` (rejects empty `{}`, extra keys, missing keys, bools, strings, NaN/Inf, swapped).
    Returns:
      `{available, lower, upper, status, strictly_positive, schema_valid, label, diagnostic}`
    """
    if value is None:
        return {
            "label": label,
            "available": False,
            "lower": None,
            "upper": None,
            "status": "CI_UNAVAILABLE_INSUFFICIENT_SAMPLE",
            "strictly_positive": False,
            "schema_valid": True,
            "diagnostic": "CI is None (insufficient clusters or observations); treated as INCONCLUSIVE.",
        }

    lo_raw: Any = None
    hi_raw: Any = None
    if isinstance(value, (list, tuple)):
        if len(value) != 2:
            return {
                "label": label,
                "available": False,
                "lower": None,
                "upper": None,
                "status": "CI_INVALID_SCHEMA",
                "strictly_positive": False,
                "schema_valid": False,
                "diagnostic": f"Expected sequence of length 2, got length {len(value)}.",
            }
        lo_raw, hi_raw = value[0], value[1]
    elif isinstance(value, Mapping):
        keys = set(value.keys())
        if keys != {"ci_low", "ci_high"}:
            return {
                "label": label,
                "available": False,
                "lower": None,
                "upper": None,
                "status": "CI_INVALID_SCHEMA",
                "strictly_positive": False,
                "schema_valid": False,
                "diagnostic": f"Malformed CI mapping keys {sorted(str(k) for k in keys)}; expected exact {{'ci_low','ci_high'}}.",
            }
        lo_raw, hi_raw = value["ci_low"], value["ci_high"]
    else:
        return {
            "label": label,
            "available": False,
            "lower": None,
            "upper": None,
            "status": "CI_INVALID_SCHEMA",
            "strictly_positive": False,
            "schema_valid": False,
            "diagnostic": f"Unsupported CI payload type {type(value).__name__}.",
        }

    if not _is_strict_finite_number(lo_raw) or not _is_strict_finite_number(hi_raw):
        return {
            "label": label,
            "available": False,
            "lower": None,
            "upper": None,
            "status": "CI_INVALID_SCHEMA",
            "strictly_positive": False,
            "schema_valid": False,
            "diagnostic": f"Non-numeric, bool, or non-finite CI endpoint(s): lo={lo_raw!r}, hi={hi_raw!r}.",
        }

    lo = float(lo_raw)
    hi = float(hi_raw)
    if lo > hi:
        return {
            "label": label,
            "available": False,
            "lower": None,
            "upper": None,
            "status": "CI_INVALID_SCHEMA",
            "strictly_positive": False,
            "schema_valid": False,
            "diagnostic": f"Swapped CI bounds: lower={lo} > upper={hi}.",
        }

    if lo > 0.0:
        st = "CI_VALID_POSITIVE"
        pos = True
        diag = f"Entire 95% CI strictly positive [{lo}, {hi}]."
    elif hi < 0.0:
        st = "CI_VALID_NEGATIVE"
        pos = False
        diag = f"Entire 95% CI strictly negative [{lo}, {hi}] (upper < 0; does NOT cross zero)."
    elif lo < 0.0 < hi:
        st = "CI_STRADDLES_ZERO"
        pos = False
        diag = f"95% CI straddles zero [{lo}, {hi}]."
    else:
        # Exact zero at lower, upper, or both endpoints ([0, x], [x, 0], [0, 0])
        st = "CI_TOUCHES_ZERO"
        pos = False
        diag = f"95% CI touches zero boundary [{lo}, {hi}]; exact zero is NOT strictly positive."

    return {
        "label": label,
        "available": True,
        "lower": round(lo, 6),
        "upper": round(hi, 6),
        "status": st,
        "strictly_positive": pos,
        "schema_valid": True,
        "diagnostic": diag,
    }


def evaluate_kh03_dev_ci_and_advancement(
    *,
    arm_code: str,
    closed_trades_1x: int,
    effective_ny_days_1x: int,
    expectancy_r_1x: Optional[float],
    net_pnl_usd_1x: Optional[float],
    expectancy_r_fee_only_0p0006: Optional[float],
    expectancy_r_2x: Optional[float],
    raw_ny_day_95ci_r: Any,
    holm_bonferroni_adjusted_pvalue: Optional[float],
    matching_quality_status: Optional[str],
    matched_mean_delta_r_vs_null: Optional[float],
    expectancy_r_vs_placebo_shuffled: Optional[float],
    expectancy_r_vs_time_offset: Optional[float],
    wf_median_fold_expectancy_r: Optional[float],
    wf_positive_fold_ratio: Optional[float],
    wf_valid_forward_only_folds_count: int,
    executable_edge_eligible: bool = True,
    disqualification_reason: Optional[str] = None,
    min_closed_trades: int = 50,
    min_effective_ny_days: int = 10,
) -> Dict[str, Any]:
    """
    Pure raw-evidence DEV advancement gate (P0-SCI-01).
    NEVER reads or trusts any self-reported `passes_preregistered_dev_advancement` boolean;
    recomputes every pre-registered criterion directly from raw metrics & raw CI bounds.
    """
    ci_parsed = parse_kh03_cluster_ci_strict(raw_ny_day_95ci_r, label=f"{arm_code}:ny_day_cluster_95ci_r")
    ci_status = str(ci_parsed["status"])
    ci_ok = bool(
        ci_parsed["available"]
        and ci_parsed["schema_valid"]
        and ci_parsed["strictly_positive"]
        and ci_parsed["lower"] is not None
        and ci_parsed["upper"] is not None
        and float(ci_parsed["lower"]) > 0.0
        and float(ci_parsed["upper"]) >= float(ci_parsed["lower"])
    )

    n_ok = int(closed_trades_1x) >= int(min_closed_trades) and int(effective_ny_days_1x) >= int(min_effective_ny_days)
    pos_1x = (
        expectancy_r_1x is not None
        and _is_strict_finite_number(expectancy_r_1x)
        and float(expectancy_r_1x) > 0.0
        and net_pnl_usd_1x is not None
        and _is_strict_finite_number(net_pnl_usd_1x)
        and float(net_pnl_usd_1x) > 0.0
    )
    pos_fo = (
        expectancy_r_fee_only_0p0006 is not None
        and _is_strict_finite_number(expectancy_r_fee_only_0p0006)
        and float(expectancy_r_fee_only_0p0006) > 0.0
    )
    pos_2x = (
        expectancy_r_2x is not None
        and _is_strict_finite_number(expectancy_r_2x)
        and float(expectancy_r_2x) > 0.0
    )
    p_ok = (
        holm_bonferroni_adjusted_pvalue is not None
        and _is_strict_finite_number(holm_bonferroni_adjusted_pvalue)
        and 0.0 <= float(holm_bonferroni_adjusted_pvalue) < 0.05
    )
    match_ok = (
        matching_quality_status == "BALANCED_COMMON_SUPPORT"
        and matched_mean_delta_r_vs_null is not None
        and _is_strict_finite_number(matched_mean_delta_r_vs_null)
        and float(matched_mean_delta_r_vs_null) > 0.0
    )
    ctrl_ok = (
        expectancy_r_vs_placebo_shuffled is not None
        and _is_strict_finite_number(expectancy_r_vs_placebo_shuffled)
        and float(expectancy_r_vs_placebo_shuffled) > 0.0
        and expectancy_r_vs_time_offset is not None
        and _is_strict_finite_number(expectancy_r_vs_time_offset)
        and float(expectancy_r_vs_time_offset) > 0.0
    )
    wf_ok = (
        int(wf_valid_forward_only_folds_count) >= 2
        and wf_median_fold_expectancy_r is not None
        and _is_strict_finite_number(wf_median_fold_expectancy_r)
        and float(wf_median_fold_expectancy_r) > 0.0
        and wf_positive_fold_ratio is not None
        and _is_strict_finite_number(wf_positive_fold_ratio)
        and float(wf_positive_fold_ratio) >= 0.60
    )

    failed_criteria: List[str] = []
    if not executable_edge_eligible:
        failed_criteria.append(disqualification_reason or "ARM_DISQUALIFIED_NOT_EXECUTABLE_EDGE_CANDIDATE")
    if not n_ok:
        failed_criteria.append(f"INSUFFICIENT_SAMPLE_OR_DAYS:n={closed_trades_1x},days={effective_ny_days_1x}")
    if not pos_1x:
        failed_criteria.append(f"NON_POSITIVE_1X_NET:expR={expectancy_r_1x},net={net_pnl_usd_1x}")
    if not pos_fo:
        failed_criteria.append(f"NON_POSITIVE_FEE_ONLY_0p0006:expR={expectancy_r_fee_only_0p0006}")
    if not pos_2x:
        failed_criteria.append(f"NON_POSITIVE_2X_STRESS:expR={expectancy_r_2x}")

    if not ci_ok:
        if ci_status == "CI_VALID_NEGATIVE":
            failed_criteria.append(
                f"NY_DAY_CI_UPPER_BELOW_OR_EQUAL_ZERO:ci=[{ci_parsed['lower']},{ci_parsed['upper']}]"
            )
        elif ci_status == "CI_STRADDLES_ZERO":
            failed_criteria.append(
                f"NY_DAY_CI_STRADDLES_ZERO:ci=[{ci_parsed['lower']},{ci_parsed['upper']}]"
            )
        elif ci_status == "CI_TOUCHES_ZERO":
            failed_criteria.append(
                f"NY_DAY_CI_TOUCHES_ZERO_BOUNDARY:ci=[{ci_parsed['lower']},{ci_parsed['upper']}]"
            )
        elif ci_status == "CI_UNAVAILABLE_INSUFFICIENT_SAMPLE":
            failed_criteria.append("NY_DAY_CI_UNAVAILABLE_INSUFFICIENT_SAMPLE")
        else:
            failed_criteria.append(f"NY_DAY_CI_INVALID_SCHEMA:{ci_parsed['diagnostic']}")

    if not p_ok:
        failed_criteria.append(f"HOLM_BONFERRONI_P_GE_0p05:p_adj={holm_bonferroni_adjusted_pvalue}")
    if not match_ok:
        failed_criteria.append(
            f"MATCHED_NULL_LIFT_FAILED:mq={matching_quality_status},delta={matched_mean_delta_r_vs_null}"
        )
    if not ctrl_ok:
        failed_criteria.append("FAILED_TO_BEAT_PLACEBO_AND_TIME_OFFSET_CONTROLS")
    if not wf_ok:
        failed_criteria.append(
            f"FORWARD_ONLY_WF_UNSTABLE:folds={wf_valid_forward_only_folds_count},"
            f"med={wf_median_fold_expectancy_r},pos_ratio={wf_positive_fold_ratio}"
        )

    ci_schema_gate_pass = bool(ci_parsed["schema_valid"])
    passes_all = len(failed_criteria) == 0 and ci_schema_gate_pass

    if not ci_schema_gate_pass:
        arm_verdict = "FAIL_INVALID_CI_SCHEMA"
    elif not executable_edge_eligible:
        arm_verdict = "DISQUALIFIED_FILTER_ONLY_RESEARCH_ADVISORY"
    elif passes_all:
        arm_verdict = "DEV_CANDIDATE_UNPROVEN_OOS"
    elif ci_status == "CI_UNAVAILABLE_INSUFFICIENT_SAMPLE" and pos_1x and pos_fo and pos_2x:
        arm_verdict = "INCONCLUSIVE_INSUFFICIENT_CI_SAMPLE"
    elif pos_1x and pos_2x and not n_ok:
        arm_verdict = "INCONCLUSIVE_LOW_POWER"
    else:
        arm_verdict = "REJECTED_NO_DEV_ADVANCEMENT"

    return {
        "arm_code": arm_code,
        "ci_parsed": ci_parsed,
        "ci_schema_valid": ci_schema_gate_pass,
        "subgates": {
            "executable_edge_eligible": bool(executable_edge_eligible),
            "sample_and_days_ok": n_ok,
            "positive_1x_ok": pos_1x,
            "positive_fee_only_ok": pos_fo,
            "positive_2x_ok": pos_2x,
            "ci_strictly_positive_ok": ci_ok,
            "holm_pvalue_ok": p_ok,
            "matched_null_ok": match_ok,
            "beats_controls_ok": ctrl_ok,
            "forward_only_wf_ok": wf_ok,
        },
        "failed_advancement_criteria": failed_criteria,
        "passes_preregistered_dev_advancement": passes_all,
        "arm_dev_verdict": arm_verdict,
    }


def build_kh03_r2_ci_red_green_and_boundary_artifacts(
    *,
    prior_r1_15_path: Path,
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """
    Produces Artifacts 03, 04, and 05 for P0-SCI-01:
      - `03_ci_old_bug_red_proof.json` (real R1 artifact 15 + old Mapping parser reproduction)
      - `04_ci_strict_adapter_green_boundary_matrix.json` (14 strict boundary fixtures)
      - `05_ci_independent_positive_negative_verdicts.json` (synthetic positive PASS, positive-CI-only FAIL,
        missing-CI INCONCLUSIVE, invalid-CI FAIL, negative real R1 arms properly classified)
    """
    r1_15 = json.loads(prior_r1_15_path.read_text("utf-8"))
    r1_arms = r1_15.get("primary_arm_evaluations") or {}

    def _old_r1_buggy_parse(raw_ci: Any) -> Tuple[Optional[float], str]:
        ci_ny = raw_ci or {}
        ci_lo = ci_ny.get("ci_low") if isinstance(ci_ny, Mapping) else None
        ok = ci_lo is not None and float(ci_lo) > 0.0
        return ci_lo, ("PASS" if ok else f"NY_DAY_95CI_CROSSES_ZERO:ci_low={ci_lo}")

    r1_repro_rows: Dict[str, Any] = {}
    for ac, st in sorted(r1_arms.items()):
        raw_ci = st.get("ny_day_cluster_95ci_r")
        old_lo, old_reason = _old_r1_buggy_parse(raw_ci)
        new_p = parse_kh03_cluster_ci_strict(raw_ci, label=f"{ac}:r1_raw_ci")
        r1_repro_rows[ac] = {
            "r1_raw_ci_value": raw_ci,
            "r1_raw_ci_python_type": type(raw_ci).__name__,
            "r1_published_failed_criteria": list(st.get("failed_advancement_criteria") or ()),
            "old_r1_parser_extracted_ci_low": old_lo,
            "old_r1_parser_emitted_reason": old_reason,
            "r1_bug_confirmed_false_crosses_zero_with_none": (
                isinstance(raw_ci, list)
                and len(raw_ci) == 2
                and float(raw_ci[1]) < 0.0
                and old_lo is None
                and "NY_DAY_95CI_CROSSES_ZERO:ci_low=None" in (st.get("failed_advancement_criteria") or ())
            ),
            "r2_strict_parsed_status": new_p["status"],
            "r2_strict_lower": new_p["lower"],
            "r2_strict_upper": new_p["upper"],
        }

    # Also prove old R1 parser falsely rejected a strictly positive list [0.25, 0.55]
    pos_old_lo, pos_old_reason = _old_r1_buggy_parse([0.25, 0.55])
    pos_new = parse_kh03_cluster_ci_strict([0.25, 0.55], label="synthetic_positive_list")

    f03: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH03_R2,
        "schema_version": SCHEMA_VERSION_KH03_R2,
        "classification": "REAL_RED",
        "source_r1_artifact_rel": "aurum-desk/docs/artifacts/kh_03_research_data/20261123T055123Z_f0b668dc0dd1/15_purged_walkforward_bootstrap_multiplicity_power.json",
        "source_r1_artifact_sha256": _sha256_file(prior_r1_15_path),
        "bug_location": "aurum-desk/backend/services/kh_03_research_service.py:run_kh03_full_research_study_10_to_16:901-902",
        "bug_explanation": (
            "`_block_bootstrap_ci()` returns `Optional[List[float]]` (`[lower, upper]`), while KH-03.R1 checked "
            "`isinstance(ci_ny, Mapping)` and read `ci_ny.get('ci_low')`, yielding `ci_low=None` on EVERY list CI. "
            "Consequently all 3 strictly negative R1 arms were mislabeled `NY_DAY_95CI_CROSSES_ZERO:ci_low=None`, "
            "and even a strictly positive list `[0.25, 0.55]` would silently fail with `ci_low=None`."
        ),
        "r1_real_arms_reproduction": r1_repro_rows,
        "synthetic_positive_list_false_rejection_proof": {
            "input": [0.25, 0.55],
            "old_r1_extracted_ci_low": pos_old_lo,
            "old_r1_emitted_reason": pos_old_reason,
            "r2_strict_status": pos_new["status"],
            "r2_strictly_positive": pos_new["strictly_positive"],
        },
        "all_3_r1_arms_bug_reproduced": all(
            bool(v["r1_bug_confirmed_false_crosses_zero_with_none"]) for v in r1_repro_rows.values()
        ),
        "status": (
            "PASS"
            if (
                len(r1_repro_rows) == 3
                and all(bool(v["r1_bug_confirmed_false_crosses_zero_with_none"]) for v in r1_repro_rows.values())
                and pos_old_lo is None
                and pos_new["status"] == "CI_VALID_POSITIVE"
            )
            else "FAIL"
        ),
    }
    f03["artifact_canonical_sha256"] = canonical_hash({k: v for k, v in f03.items() if k != "artifact_canonical_sha256"})

    # 04: 14 strict boundary cases
    boundary_specs: List[Tuple[str, Any, str, bool, bool]] = [
        ("B01_NEGATIVE_LIST", [-1.0184, -0.8625], "CI_VALID_NEGATIVE", False, True),
        ("B02_POSITIVE_LIST", [0.12, 0.48], "CI_VALID_POSITIVE", True, True),
        ("B03_STRADDLES_ZERO", [-0.12, 0.04], "CI_STRADDLES_ZERO", False, True),
        ("B04_TOUCHES_ZERO_LOW", [0.0, 0.30], "CI_TOUCHES_ZERO", False, True),
        ("B05_TOUCHES_ZERO_HIGH", [-0.30, 0.0], "CI_TOUCHES_ZERO", False, True),
        ("B06_IDENTICAL_POSITIVE_BOUNDS", [0.15, 0.15], "CI_VALID_POSITIVE", True, True),
        ("B07_SWAPPED_BOUNDS", [0.30, 0.10], "CI_INVALID_SCHEMA", False, False),
        ("B08_NAN_BOUND", [float("nan"), 0.50], "CI_INVALID_SCHEMA", False, False),
        ("B09_INF_BOUND", [0.10, float("inf")], "CI_INVALID_SCHEMA", False, False),
        ("B10_BOOL_COERCION_REJECTED", [True, True], "CI_INVALID_SCHEMA", False, False),
        ("B11_STRING_COERCION_REJECTED", ["0.10", "0.40"], "CI_INVALID_SCHEMA", False, False),
        ("B12_MISSING_NONE_INCONCLUSIVE", None, "CI_UNAVAILABLE_INSUFFICIENT_SAMPLE", False, True),
        ("B13_EMPTY_MAPPING_REJECTED", {}, "CI_INVALID_SCHEMA", False, False),
        ("B14_STRICT_VALID_MAPPING_COMPAT", {"ci_low": 0.08, "ci_high": 0.32}, "CI_VALID_POSITIVE", True, True),
    ]
    b_rows: List[Dict[str, Any]] = []
    b_all_ok = True
    for cid, val, exp_st, exp_pos, exp_schema_ok in boundary_specs:
        res = parse_kh03_cluster_ci_strict(val, label=cid)
        ok = (
            res["status"] == exp_st
            and bool(res["strictly_positive"]) == exp_pos
            and bool(res["schema_valid"]) == exp_schema_ok
        )
        if not ok:
            b_all_ok = False
        b_rows.append(
            {
                "case_id": cid,
                "input_repr": repr(val),
                "expected_status": exp_st,
                "actual_status": res["status"],
                "expected_strictly_positive": exp_pos,
                "actual_strictly_positive": res["strictly_positive"],
                "expected_schema_valid": exp_schema_ok,
                "actual_schema_valid": res["schema_valid"],
                "lower": res["lower"],
                "upper": res["upper"],
                "passed": ok,
            }
        )

    f04: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH03_R2,
        "schema_version": SCHEMA_VERSION_KH03_R2,
        "total_boundary_cases": len(b_rows),
        "passed_boundary_cases": sum(1 for r in b_rows if r["passed"]),
        "boundary_matrix": b_rows,
        "status": "PASS" if b_all_ok else "FAIL",
    }
    f04["artifact_canonical_sha256"] = canonical_hash({k: v for k, v in f04.items() if k != "artifact_canonical_sha256"})

    # 05: Synthetic positive vs positive-CI-only vs negative vs unavailable vs invalid full gate verdicts
    synth_full_pass = evaluate_kh03_dev_ci_and_advancement(
        arm_code="SYNTH_POSITIVE_ALL_GATES",
        closed_trades_1x=85,
        effective_ny_days_1x=14,
        expectancy_r_1x=0.32,
        net_pnl_usd_1x=68.0,
        expectancy_r_fee_only_0p0006=0.24,
        expectancy_r_2x=0.14,
        raw_ny_day_95ci_r=[0.11, 0.53],
        holm_bonferroni_adjusted_pvalue=0.012,
        matching_quality_status="BALANCED_COMMON_SUPPORT",
        matched_mean_delta_r_vs_null=0.28,
        expectancy_r_vs_placebo_shuffled=0.31,
        expectancy_r_vs_time_offset=0.27,
        wf_median_fold_expectancy_r=0.21,
        wf_positive_fold_ratio=1.0,
        wf_valid_forward_only_folds_count=3,
        executable_edge_eligible=True,
    )
    synth_pos_ci_other_fail = evaluate_kh03_dev_ci_and_advancement(
        arm_code="SYNTH_POS_CI_ONLY_OTHER_GATES_FAIL",
        closed_trades_1x=12,
        effective_ny_days_1x=3,
        expectancy_r_1x=0.25,
        net_pnl_usd_1x=7.5,
        expectancy_r_fee_only_0p0006=0.10,
        expectancy_r_2x=-0.08,
        raw_ny_day_95ci_r=[0.05, 0.45],
        holm_bonferroni_adjusted_pvalue=0.22,
        matching_quality_status="MATCHING_INADEQUATE",
        matched_mean_delta_r_vs_null=-0.02,
        expectancy_r_vs_placebo_shuffled=-0.05,
        expectancy_r_vs_time_offset=-0.03,
        wf_median_fold_expectancy_r=-0.10,
        wf_positive_fold_ratio=0.33,
        wf_valid_forward_only_folds_count=3,
        executable_edge_eligible=True,
    )
    synth_neg_strict = evaluate_kh03_dev_ci_and_advancement(
        arm_code="SYNTH_STRICT_NEGATIVE_LIKE_R1",
        closed_trades_1x=75,
        effective_ny_days_1x=14,
        expectancy_r_1x=-0.9749,
        net_pnl_usd_1x=-180.434,
        expectancy_r_fee_only_0p0006=-0.8459,
        expectancy_r_2x=-1.0203,
        raw_ny_day_95ci_r=[-1.0184, -0.8625],
        holm_bonferroni_adjusted_pvalue=1.0,
        matching_quality_status="BALANCED_COMMON_SUPPORT",
        matched_mean_delta_r_vs_null=-0.1167,
        expectancy_r_vs_placebo_shuffled=0.0102,
        expectancy_r_vs_time_offset=-0.0629,
        wf_median_fold_expectancy_r=-1.015,
        wf_positive_fold_ratio=0.0,
        wf_valid_forward_only_folds_count=3,
        executable_edge_eligible=True,
    )
    synth_missing_ci = evaluate_kh03_dev_ci_and_advancement(
        arm_code="SYNTH_MISSING_CI_INCONCLUSIVE",
        closed_trades_1x=60,
        effective_ny_days_1x=12,
        expectancy_r_1x=0.20,
        net_pnl_usd_1x=30.0,
        expectancy_r_fee_only_0p0006=0.12,
        expectancy_r_2x=0.05,
        raw_ny_day_95ci_r=None,
        holm_bonferroni_adjusted_pvalue=0.02,
        matching_quality_status="BALANCED_COMMON_SUPPORT",
        matched_mean_delta_r_vs_null=0.15,
        expectancy_r_vs_placebo_shuffled=0.18,
        expectancy_r_vs_time_offset=0.14,
        wf_median_fold_expectancy_r=0.11,
        wf_positive_fold_ratio=0.67,
        wf_valid_forward_only_folds_count=3,
        executable_edge_eligible=True,
    )
    synth_invalid_ci = evaluate_kh03_dev_ci_and_advancement(
        arm_code="SYNTH_INVALID_CI_SCHEMA",
        closed_trades_1x=85,
        effective_ny_days_1x=14,
        expectancy_r_1x=0.30,
        net_pnl_usd_1x=60.0,
        expectancy_r_fee_only_0p0006=0.20,
        expectancy_r_2x=0.10,
        raw_ny_day_95ci_r={"bogus": 1},
        holm_bonferroni_adjusted_pvalue=0.01,
        matching_quality_status="BALANCED_COMMON_SUPPORT",
        matched_mean_delta_r_vs_null=0.20,
        expectancy_r_vs_placebo_shuffled=0.22,
        expectancy_r_vs_time_offset=0.19,
        wf_median_fold_expectancy_r=0.15,
        wf_positive_fold_ratio=1.0,
        wf_valid_forward_only_folds_count=3,
        executable_edge_eligible=True,
    )

    v05_ok = (
        synth_full_pass["passes_preregistered_dev_advancement"] is True
        and synth_full_pass["arm_dev_verdict"] == "DEV_CANDIDATE_UNPROVEN_OOS"
        and synth_pos_ci_other_fail["subgates"]["ci_strictly_positive_ok"] is True
        and synth_pos_ci_other_fail["passes_preregistered_dev_advancement"] is False
        and synth_neg_strict["ci_parsed"]["status"] == "CI_VALID_NEGATIVE"
        and any(c.startswith("NY_DAY_CI_UPPER_BELOW_OR_EQUAL_ZERO") for c in synth_neg_strict["failed_advancement_criteria"])
        and not any("CROSSES_ZERO" in c for c in synth_neg_strict["failed_advancement_criteria"])
        and synth_missing_ci["passes_preregistered_dev_advancement"] is False
        and synth_missing_ci["arm_dev_verdict"] == "INCONCLUSIVE_INSUFFICIENT_CI_SAMPLE"
        and synth_invalid_ci["ci_schema_valid"] is False
        and synth_invalid_ci["arm_dev_verdict"] == "FAIL_INVALID_CI_SCHEMA"
    )
    f05: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH03_R2,
        "schema_version": SCHEMA_VERSION_KH03_R2,
        "synthetic_fixtures_never_prove_real_edge": True,
        "synthetic_verdicts": {
            "synth_full_pass": synth_full_pass,
            "synth_pos_ci_other_fail": synth_pos_ci_other_fail,
            "synth_neg_strict": synth_neg_strict,
            "synth_missing_ci": synth_missing_ci,
            "synth_invalid_ci": synth_invalid_ci,
        },
        "status": "PASS" if v05_ok else "FAIL",
    }
    f05["artifact_canonical_sha256"] = canonical_hash({k: v for k, v in f05.items() if k != "artifact_canonical_sha256"})
    return f03, f04, f05


# ==============================================================================
# SECTION B — P0-SCI-02: H3 POLICY TRUTH (PATH B FILTER-ONLY RESEARCH ADVISORY)
# ==============================================================================


def assert_no_instantaneous_open_to_open_fill(
    *,
    observed_at_ms: int,
    assumed_tradeable_at_ms: Optional[int],
    observed_l1_bbo_available: bool,
) -> Dict[str, Any]:
    """
    Causal execution-availability invariant (§05.2):
    Observing `bar[t+1].open` at `observed_at_ms` and claiming guaranteed fill at that EXACT
    same timestamp/price (`assumed_tradeable_at_ms == observed_at_ms` or `None` without
    post-open observed L1 BBO / latency quote) is NOT a causally verifiable executable fill.
    """
    if not observed_l1_bbo_available:
        return {
            "executable_fill_verifiable": False,
            "reason": "NO_OBSERVED_L1_BBO_OR_POST_OPEN_TICK_QUOTE_IN_M1_OHLCV",
        }
    if assumed_tradeable_at_ms is None or int(assumed_tradeable_at_ms) <= int(observed_at_ms):
        return {
            "executable_fill_verifiable": False,
            "reason": "OPEN_TO_OPEN_INSTANTANEOUS_FILL_AFTER_OBSERVATION_FORBIDDEN",
        }
    return {
        "executable_fill_verifiable": True,
        "reason": "POST_OBSERVATION_LATENCY_VERIFIED_QUOTE_AVAILABLE",
    }


def build_kh03_r2_h3_red_proof_and_policy_choice(
    *,
    datasets: Mapping[str, Any],
    mtf_bundle: Mapping[str, Any],
    r2_cands_1x: Mapping[str, Any],
    tested_code_sha: str,
    observed_l1_bbo_available: bool = False,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Produces Artifacts 06 and 07 for P0-SCI-02:
      - `06_h3_old_filter_only_vs_resize_red_proof.json`: proves on BOTH real DEV (1,323 opportunities,
        881 resize proposals, 156 replayed episodes where R1 discarded `final_qty`) AND synthetic
        LONG/SHORT cases (plus gap/SL/TP/min-lot/high-low future mutation checks) that R1 H3
        discarded `final_qty` while authoritative simulator kept original `decision_qty`.
      - `07_h3_policy_choice_and_execution_feasibility.json`: frozen BEFORE recomputing H3 results,
        selecting PATH B (`H3_FILL_TIME_FILTER_ONLY_RESEARCH_ADVISORY`) honestly because M1 OHLCV
        has `observed_l1_bbo_available=False` and cannot verify post-open executable fills.
    """
    bars_1m = list(datasets["1m"])
    base_null = sorted(
        list((r2_cands_1x.get("arm_candidates") or {}).get("NC_NULL_TRIGGER") or ()),
        key=lambda c: (int(c["decision_timestamp_ms"]), str(c["direction"]), str(c["opportunity_id"])),
    )

    action_counts: Counter = Counter()
    resized_by_opp: Dict[str, Dict[str, Any]] = {}
    for c in base_null:
        idx = int(c["checkpoint_index_1m"])
        if idx + 1 >= len(bars_1m):
            action_counts["NO_NEXT_BAR"] += 1
            continue
        dq = float(c.get("hypothetical_quantity") or c.get("executed_quantity") or 0.01)
        ft = evaluate_causal_fill_time_policy_single_trade(
            direction=str(c["direction"]),
            decision_entry=float(c["entry_reference"]),
            fill_bar_open=float(bars_1m[idx + 1]["open"]),
            selected_sl=float(c["selected_sl"]),
            take_profit=float(c["take_profit"]),
            decision_qty=dq,
            spread_usd=DEFAULT_MODELED_SPREAD_USD,
            slippage_usd=DEFAULT_SLIPPAGE_USD,
            risk_cap_usd=PLANNED_NET_RISK_CAP_USD,
            min_net_rr=MIN_NET_RR_THRESHOLD,
            now_ms=int(bars_1m[idx + 1]["open_time"]),
        )
        act = str(ft["fill_time_action"])
        action_counts[act] += 1
        if act == "RESIZED_CAUSALLY_AT_FILL_TIME":
            resized_by_opp[str(c["opportunity_id"])] = {
                "opportunity_id": str(c["opportunity_id"]),
                "direction": str(c["direction"]),
                "decision_qty": round(dq, 4),
                "proposed_final_qty": round(float(ft["final_qty"]), 4),
                "fill_bar_open": float(bars_1m[idx + 1]["open"]),
                "fill_time_planned_net_risk_usd": ft["fill_time_planned_net_risk_usd"],
                "fill_time_net_rr": ft["fill_time_net_rr"],
            }

    # Replay R1 H3 as-is to prove R1 discarded final_qty on real DEV episodes
    kh03_cands_1x = build_kh03_arm_candidates_from_r2_universe(
        datasets=datasets, mtf_bundle=mtf_bundle, r2_cands_bundle=r2_cands_1x,
        spread_usd=DEFAULT_MODELED_SPREAD_USD, slippage_usd=DEFAULT_SLIPPAGE_USD,
    )
    _rep_h3, eps_h3 = _replay_chronological_custom_cost_scenario(
        arm_code=KH03_R2_H3_ADVISORY_ARM,
        scenario_id="1X_BASE_0p0004",
        candidates=kh03_cands_1x["arm_candidates"][KH03_R2_H3_ADVISORY_ARM],
        bars_1m=bars_1m,
        spread_usd=DEFAULT_MODELED_SPREAD_USD,
        slippage_usd=DEFAULT_SLIPPAGE_USD,
        taker_fee_rate=0.0004,
        maker_fee_rate=0.0002,
        return_episodes=True,
    )
    discarded_real_dev: List[Dict[str, Any]] = []
    for e in eps_h3:
        oid = str(e["opportunity_id"])
        if oid in resized_by_opp:
            rinfo = resized_by_opp[oid]
            sim_q = round(float(e["executed_quantity"]), 4)
            prop_q = float(rinfo["proposed_final_qty"])
            if abs(sim_q - prop_q) > 1e-6:
                discarded_real_dev.append(
                    {
                        "opportunity_id": oid,
                        "direction": e["direction"],
                        "decision_qty": rinfo["decision_qty"],
                        "proposed_final_qty": prop_q,
                        "r1_authoritative_simulated_qty": sim_q,
                        "final_qty_discarded_in_r1": True,
                        "outcome_label": e["outcome_label"],
                    }
                )

    # Synthetic LONG & SHORT RED proofs + edge-case verification (gap past SL, past TP, min-lot, high-low reject)
    synth_long_ft = evaluate_causal_fill_time_policy_single_trade(
        direction="LONG", decision_entry=4125.00, fill_bar_open=4125.50,
        selected_sl=4122.00, take_profit=4150.00, decision_qty=0.45,
        spread_usd=0.20, slippage_usd=0.10, risk_cap_usd=2.50, min_net_rr=2.0, now_ms=1791500060000,
    )
    synth_short_ft = evaluate_causal_fill_time_policy_single_trade(
        direction="SHORT", decision_entry=4125.00, fill_bar_open=4124.50,
        selected_sl=4128.00, take_profit=4100.00, decision_qty=0.45,
        spread_usd=0.20, slippage_usd=0.10, risk_cap_usd=2.50, min_net_rr=2.0, now_ms=1791500060000,
    )
    synth_gap_sl = evaluate_causal_fill_time_policy_single_trade(
        direction="LONG", decision_entry=4125.00, fill_bar_open=4121.00,
        selected_sl=4122.00, take_profit=4150.00, decision_qty=0.30,
    )
    synth_gap_tp = evaluate_causal_fill_time_policy_single_trade(
        direction="LONG", decision_entry=4125.00, fill_bar_open=4151.00,
        selected_sl=4122.00, take_profit=4150.00, decision_qty=0.30,
    )
    synth_min_lot_rej = evaluate_causal_fill_time_policy_single_trade(
        direction="LONG", decision_entry=4125.00, fill_bar_open=4400.00,
        selected_sl=4100.00, take_profit=5500.00, decision_qty=0.05,
    )
    future_hl_rejected = False
    try:
        evaluate_causal_fill_time_policy_single_trade(
            direction="LONG", decision_entry=4125.00, fill_bar_open=4125.00,
            selected_sl=4122.00, take_profit=4150.00, decision_qty=0.30,
            forbidden_future_fields_present=True,
        )
    except ValueError:
        future_hl_rejected = True

    open_to_open_check = assert_no_instantaneous_open_to_open_fill(
        observed_at_ms=1791500060000,
        assumed_tradeable_at_ms=1791500060000,
        observed_l1_bbo_available=observed_l1_bbo_available,
    )

    f06_ok = (
        len(resized_by_opp) > 0
        and len(discarded_real_dev) > 0
        and synth_long_ft["fill_time_action"] == "RESIZED_CAUSALLY_AT_FILL_TIME"
        and synth_short_ft["fill_time_action"] == "RESIZED_CAUSALLY_AT_FILL_TIME"
        and synth_gap_sl["fill_time_action"] == "REJECTED_FILL_TIME_GAPPED_PAST_SL"
        and synth_gap_tp["fill_time_action"] == "REJECTED_FILL_TIME_GAPPED_PAST_TP"
        and synth_min_lot_rej["accepted"] is False
        and future_hl_rejected is True
        and open_to_open_check["executable_fill_verifiable"] is False
    )

    f06: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH03_R2,
        "schema_version": SCHEMA_VERSION_KH03_R2,
        "classification": "REAL_RED",
        "bug_location": "aurum-desk/backend/services/kh_03_research_service.py:build_kh03_arm_candidates_from_r2_universe:393-414",
        "bug_explanation": (
            "KH-03.R1 called `evaluate_causal_fill_time_policy_single_trade()` and checked `ft['accepted']`, "
            "but appended original candidate `c` without forwarding `final_qty`. The authoritative simulator "
            "`simulate_v17931_trade_execution_net()` then recomputed size from original `planned_entry`. "
            "Additionally, observing `bar[t+1].open` and assuming guaranteed fill at that same `bar[t+1].open` "
            "violates execution-availability causality without observed post-open tick/L1 BBO quotes."
        ),
        "real_dev_universe_action_counts": dict(sorted(action_counts.items())),
        "real_dev_resized_opportunities_count": len(resized_by_opp),
        "real_dev_replayed_episodes_where_r1_discarded_final_qty_count": len(discarded_real_dev),
        "real_dev_discarded_examples": discarded_real_dev[:10],
        "synthetic_long_resize_repro": synth_long_ft,
        "synthetic_short_resize_repro": synth_short_ft,
        "synthetic_edge_controls": {
            "gap_past_sl_action": synth_gap_sl["fill_time_action"],
            "gap_past_tp_action": synth_gap_tp["fill_time_action"],
            "min_lot_breach_rejected": not bool(synth_min_lot_rej["accepted"]),
            "same_bar_high_low_future_fields_rejected": future_hl_rejected,
            "open_to_open_causality_check": open_to_open_check,
        },
        "status": "PASS" if f06_ok else "FAIL",
    }
    f06["artifact_canonical_sha256"] = canonical_hash({k: v for k, v in f06.items() if k != "artifact_canonical_sha256"})

    # 07: Frozen Policy Choice & Execution Feasibility (PATH B strictly justified by M1-only OHLCV)
    f07: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH03_R2,
        "schema_version": SCHEMA_VERSION_KH03_R2,
        "frozen_before_r2_h3_recomputation": True,
        "tested_code_sha": tested_code_sha,
        "chosen_path": "PATH_B_FILTER_ONLY_RESEARCH_ADVISORY",
        "H3_ECONOMIC_CLAIM_TIER": "H3_FILL_TIME_FILTER_ONLY_RESEARCH_ADVISORY",
        "H3_RESIZE_EXECUTION_STATUS": "NOT_VERIFIABLE_WITH_M1",
        "H3_FILTER_ONLY_RESEARCH": "PASS",
        "H3_EXECUTABLE_EDGE_CANDIDATE_ELIGIBLE": False,
        "authoritative_simulator_mutated": False,
        "dataset_execution_feasibility_audit": {
            "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_KH02_R2,
            "historical_quote_mode": "MODELED_BID_ASK",
            "observed_l1_bbo_available": bool(observed_l1_bbo_available),
            "post_open_intrabar_tick_quotes_available": False,
            "open_to_open_instantaneous_fill_permitted": False,
            "path_a_feasibility": "BLOCKED_MISSING_POST_OPEN_EXECUTABLE_QUOTES_OR_L1_BBO",
            "path_b_scientific_rationale": (
                "With M1-only modeled OHLCV (`observed_l1_bbo_available=False`), observing `bar[t+1].open` "
                "and filling at `bar[t+1].open` is an unverified instantaneous `OPEN_TO_OPEN` assumption. "
                "PATH B truthfully downgrades H3 to `H3_FILL_TIME_FILTER_ONLY_RESEARCH_ADVISORY`, records "
                "`proposed_final_qty` in an advisory ledger with `size_applied_to_execution=False`, keeps "
                "legacy R1 replay strictly as `R1_FILTER_ONLY_COMPATIBILITY`, and disqualifies H3 from "
                "`EXECUTABLE_EDGE_CANDIDATE` until observed post-open L1 BBO / latency data exist."
            ),
        },
        "disclosures": [
            "H3 resize execution is NOT_VERIFIABLE_WITH_M1; do NOT claim H3_EXECUTION=PASS or materialized resize.",
            "Legacy H3 replay is preserved solely as R1_FILTER_ONLY_COMPATIBILITY descriptive screening.",
            "H3 is explicitly disqualified from EXECUTABLE_EDGE_CANDIDATE and DEV_CANDIDATE_UNPROVEN_OOS.",
        ],
        "status": "PASS",
    }
    f07["policy_choice_frozen_sha256"] = canonical_hash(
        {k: v for k, v in f07.items() if k not in ("policy_choice_frozen_sha256", "artifact_canonical_sha256")}
    )
    f07["artifact_canonical_sha256"] = canonical_hash({k: v for k, v in f07.items() if k != "artifact_canonical_sha256"})
    return f06, f07


def build_kh03_r2_h3_advisory_ledger_and_disclosure(
    *,
    datasets: Mapping[str, Any],
    mtf_bundle: Mapping[str, Any],
    r2_cands_1x: Mapping[str, Any],
    h3_policy_choice_07: Mapping[str, Any],
    out_h3_ledger_gz_path: Path,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Produces Artifacts 08 and 09 for P0-SCI-02 (PATH B):
      - `08_h3_per_episode_policy_qty_time_ledger.jsonl.gz`: streaming per-episode advisory ledger
        with all §05.3 fields (`policy_mode`, `decided_at_ms`, `observed_at_ms`,
        `assumed_tradeable_at_ms=None`, `original_qty`, `proposed_final_qty`, `actual_simulated_qty`,
        `size_applied_to_execution=False`, Decimal fees & exact gross-fee-net residual = 0).
      - `09_h3_reconciliation_research_only_or_filter_only_disclosure.json`: full accounting
        reconciliation + explicit R1 legacy compatibility disclosure + disqualification from
        `EXECUTABLE_EDGE_CANDIDATE`.
    """
    bars_1m = list(datasets["1m"])
    kh03_cands_1x = build_kh03_arm_candidates_from_r2_universe(
        datasets=datasets, mtf_bundle=mtf_bundle, r2_cands_bundle=r2_cands_1x,
        spread_usd=DEFAULT_MODELED_SPREAD_USD, slippage_usd=DEFAULT_SLIPPAGE_USD,
    )
    h3_cands = kh03_cands_1x["arm_candidates"][KH03_R2_H3_ADVISORY_ARM]
    cand_by_opp = {str(c["opportunity_id"]): c for c in h3_cands}

    rep_legacy, eps_legacy = _replay_chronological_custom_cost_scenario(
        arm_code=KH03_R2_H3_ADVISORY_ARM,
        scenario_id="1X_BASE_0p0004",
        candidates=h3_cands,
        bars_1m=bars_1m,
        spread_usd=DEFAULT_MODELED_SPREAD_USD,
        slippage_usd=DEFAULT_SLIPPAGE_USD,
        taker_fee_rate=0.0004,
        maker_fee_rate=0.0002,
        return_episodes=True,
    )

    out_h3_ledger_gz_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_gz = out_h3_ledger_gz_path.with_suffix(out_h3_ledger_gz_path.suffix + ".tmp")

    uids: List[str] = []
    row_hashes: List[str] = []
    action_breakdown: Counter = Counter()
    resize_proposed_count = 0
    size_applied_true_count = 0
    max_resid = Decimal("0")
    closed_gross = Decimal("0")
    closed_fees = Decimal("0")
    closed_net = Decimal("0")
    open_accr_fees = Decimal("0")
    closed_cnt = 0
    win_cnt = 0
    loss_cnt = 0
    open_cnt = 0

    with gzip.open(tmp_gz, "wt", encoding="utf-8") as gz:
        for seq_i, ep in enumerate(eps_legacy):
            oid = str(ep["opportunity_id"])
            c = cand_by_opp[oid]
            idx = int(c["checkpoint_index_1m"])
            dec_ts = int(ep["decision_timestamp_ms"])
            obs_ts = int(bars_1m[idx + 1]["open_time"]) if idx + 1 < len(bars_1m) else dec_ts
            orig_qty = round(float(c.get("hypothetical_quantity") or c.get("executed_quantity") or ep["executed_quantity"]), 4)
            ft = evaluate_causal_fill_time_policy_single_trade(
                direction=str(c["direction"]),
                decision_entry=float(c["entry_reference"]),
                fill_bar_open=float(bars_1m[idx + 1]["open"]),
                selected_sl=float(c["selected_sl"]),
                take_profit=float(c["take_profit"]),
                decision_qty=orig_qty,
                spread_usd=DEFAULT_MODELED_SPREAD_USD,
                slippage_usd=DEFAULT_SLIPPAGE_USD,
                risk_cap_usd=PLANNED_NET_RISK_CAP_USD,
                min_net_rr=MIN_NET_RR_THRESHOLD,
                now_ms=obs_ts,
            )
            prop_qty = round(float(ft.get("final_qty") or 0.0), 4)
            sim_qty = round(float(ep["executed_quantity"]), 4)
            act = str(ft["fill_time_action"])
            action_breakdown[act] += 1
            if act == "RESIZED_CAUSALLY_AT_FILL_TIME":
                resize_proposed_count += 1

            # PATH B strictly NEVER applies proposed_final_qty to simulated P&L
            size_applied = False
            if size_applied:
                size_applied_true_count += 1

            g_d = Decimal(str(ep["realized_gross_pnl_usd"]))
            ef_d = Decimal(str(ep["entry_fee_usd"]))
            xf_d = Decimal(str(ep["exit_fee_usd"]))
            tf_d = Decimal(str(ep["total_fees_usd"]))
            af_d = Decimal(str(ep["accrued_open_entry_fee_usd"]))
            n_d = Decimal(str(ep["realized_net_pnl_usd"]))
            res_d = abs((g_d - tf_d) - n_d) if ep["outcome_label"] in ("WIN", "LOSS") else Decimal("0")
            if res_d > max_resid:
                max_resid = res_d
            if ep["outcome_label"] in ("WIN", "LOSS"):
                closed_cnt += 1
                if ep["outcome_label"] == "WIN":
                    win_cnt += 1
                else:
                    loss_cnt += 1
                closed_gross += g_d
                closed_fees += tf_d
                closed_net += n_d
            elif ep["outcome_label"] == "OPEN_AT_HORIZON":
                open_cnt += 1
                open_accr_fees += af_d

            uid = f"KH03_R2_H3_ADV|{dec_ts}|{oid}|{seq_i}"
            row = {
                "episode_uid": uid,
                "opportunity_id": oid,
                "direction": str(ep["direction"]),
                "policy_mode": "H3_FILL_TIME_FILTER_ONLY_RESEARCH_ADVISORY",
                "decided_at_ms": dec_ts,
                "observed_at_ms": obs_ts,
                "assumed_tradeable_at_ms": None,
                "decision_data_cutoff_ms": dec_ts,
                "execution_data_cutoff_ms": obs_ts,
                "entry_price_origin": "R1_FILTER_ONLY_COMPATIBILITY_NEXT_BAR_OPEN_MODELED_BID_ASK",
                "fill_time_action": act,
                "original_qty": orig_qty,
                "proposed_final_qty": prop_qty,
                "actual_simulated_qty": sim_qty,
                "advisory_qty_delta": round(prop_qty - orig_qty, 4),
                "size_applied_to_execution": size_applied,
                "fill_time_planned_net_risk_usd": ft.get("fill_time_planned_net_risk_usd"),
                "fill_time_net_rr": ft.get("fill_time_net_rr"),
                "filled": bool(ep["filled"]),
                "outcome_label": str(ep["outcome_label"]),
                "planned_net_risk_usd": round(float(ep["planned_net_risk_usd"]), 4),
                "realized_gross_pnl_usd": _dec4(g_d),
                "entry_fee_usd": _dec4(ef_d),
                "exit_fee_usd": _dec4(xf_d),
                "total_fees_usd": _dec4(tf_d),
                "accrued_open_entry_fee_usd": _dec4(af_d),
                "realized_net_pnl_usd": _dec4(n_d),
                "gross_fee_net_residual_usd": _dec4(res_d),
                "status": "ADVISORY_ONLY_SIZE_NOT_APPLIED_TO_EXECUTION",
            }
            ln = json.dumps(row, sort_keys=True, separators=(",", ":"))
            gz.write(ln + "\n")
            uids.append(uid)
            row_hashes.append(hashlib.sha256(ln.encode("utf-8")).hexdigest())

    tmp_gz.replace(out_h3_ledger_gz_path)
    ledger_sha = _sha256_file(out_h3_ledger_gz_path)

    f08_meta = {
        "ledger_filename": out_h3_ledger_gz_path.name,
        "ledger_file_sha256": ledger_sha,
        "total_episode_rows": len(uids),
        "episode_uids_sha256": canonical_hash(uids),
        "canonical_rows_sha256": canonical_hash(row_hashes),
    }

    f09: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH03_R2,
        "schema_version": SCHEMA_VERSION_KH03_R2,
        "policy_choice_frozen_sha256": h3_policy_choice_07["policy_choice_frozen_sha256"],
        "chosen_path": "PATH_B_FILTER_ONLY_RESEARCH_ADVISORY",
        "H3_ECONOMIC_CLAIM_TIER": "H3_FILL_TIME_FILTER_ONLY_RESEARCH_ADVISORY",
        "H3_RESIZE_EXECUTION_STATUS": "NOT_VERIFIABLE_WITH_M1",
        "H3_FILTER_ONLY_RESEARCH": "PASS",
        "H3_EXECUTABLE_EDGE_CANDIDATE_ELIGIBLE": False,
        "size_applied_to_execution_any_row": (size_applied_true_count > 0),
        "linked_advisory_ledger": f08_meta,
        "replayed_action_breakdown": dict(sorted(action_breakdown.items())),
        "replayed_resize_proposed_count": resize_proposed_count,
        "legacy_r1_filter_only_compatibility_summary": {
            "label": "R1_FILTER_ONLY_COMPATIBILITY_DESCRIPTIVE_ONLY_NOT_EXECUTABLE_EDGE",
            "scheduled": len(uids),
            "closed": closed_cnt,
            "wins": win_cnt,
            "losses": loss_cnt,
            "open_at_horizon": open_cnt,
            "gross_pnl_usd": _dec4(closed_gross),
            "total_fees_usd": _dec4(closed_fees),
            "accrued_open_entry_fees_usd": _dec4(open_accr_fees),
            "net_pnl_usd": _dec4(closed_net),
            "expectancy_r": rep_legacy.get("expectancy_r"),
            "max_gross_fee_net_residual_usd": _dec4(max_resid),
            "overlap_violations": int(rep_legacy.get("overlap_violations", 0)),
        },
        "r1_vs_r2_delta_disclosure": {
            "r1_claimed_estimand_class": "MATERIALIZED_RESEARCH_COUNTERFACTUAL (alters_entry_sl_tp_qty=True)",
            "r2_truthful_estimand_class": "H3_FILL_TIME_FILTER_ONLY_RESEARCH_ADVISORY (size_applied_to_execution=False)",
            "legacy_closed_trades_delta_vs_r1": 0,
            "legacy_net_pnl_delta_vs_r1_usd": 0.0,
            "candidate_advancement_eligibility_change": "DISQUALIFIED_FROM_EXECUTABLE_EDGE_CANDIDATE",
        },
        "status": "PASS" if (size_applied_true_count == 0 and max_resid <= Decimal("0.001") and len(uids) > 0) else "FAIL",
    }
    f09["artifact_canonical_sha256"] = canonical_hash({k: v for k, v in f09.items() if k != "artifact_canonical_sha256"})
    return f08_meta, f09


def validate_kh03_r2_h3_ledger_independently(
    ledger_gz_path: Path,
    f09_doc: Mapping[str, Any],
) -> Dict[str, Any]:
    """Streaming independent verifier check for `08_h3_per_episode_policy_qty_time_ledger.jsonl.gz` vs `09`."""
    violations: List[str] = []
    if not ledger_gz_path.is_file() or ledger_gz_path.stat().st_size == 0:
        return {"valid": False, "status": "FAIL", "violations": ["H3_LEDGER_MISSING_OR_EMPTY"]}

    linked = f09_doc.get("linked_advisory_ledger") or {}
    actual_sha = _sha256_file(ledger_gz_path)
    if linked.get("ledger_file_sha256") != actual_sha:
        violations.append("H3_LEDGER_FILE_SHA256_MISMATCH")

    uids: List[str] = []
    rhashes: List[str] = []
    closed_cnt = 0
    win_cnt = 0
    loss_cnt = 0
    closed_gross = Decimal("0")
    closed_fees = Decimal("0")
    closed_net = Decimal("0")
    max_resid = Decimal("0")

    required_keys = (
        "episode_uid", "opportunity_id", "policy_mode", "decided_at_ms", "observed_at_ms",
        "assumed_tradeable_at_ms", "original_qty", "proposed_final_qty", "actual_simulated_qty",
        "size_applied_to_execution", "entry_price_origin", "decision_data_cutoff_ms",
        "execution_data_cutoff_ms", "fill_time_action", "status",
    )

    with gzip.open(ledger_gz_path, "rt", encoding="utf-8") as gz:
        for ln_no, raw in enumerate(gz, 1):
            ln = raw.rstrip("\r\n")
            if not ln:
                continue
            row = json.loads(ln)
            for rk in required_keys:
                if rk not in row:
                    violations.append(f"H3_LEDGER_MISSING_FIELD:{rk}:line={ln_no}")
            if row.get("policy_mode") != "H3_FILL_TIME_FILTER_ONLY_RESEARCH_ADVISORY":
                violations.append(f"H3_LEDGER_UNEXPECTED_POLICY_MODE:{row.get('policy_mode')}:line={ln_no}")
            if row.get("size_applied_to_execution") is not False:
                violations.append(f"H3_PATH_B_CLAIMED_SIZE_APPLIED_TRUE:line={ln_no}")
            if row.get("assumed_tradeable_at_ms") is not None:
                violations.append(f"H3_PATH_B_FABRICATED_ASSUMED_TRADEABLE_TS:line={ln_no}")
            if int(row.get("observed_at_ms", 0)) < int(row.get("decided_at_ms", 0)):
                violations.append(f"H3_OBSERVED_BEFORE_DECIDED:line={ln_no}")

            if row.get("outcome_label") in ("WIN", "LOSS"):
                closed_cnt += 1
                if row["outcome_label"] == "WIN":
                    win_cnt += 1
                else:
                    loss_cnt += 1
                g_d = Decimal(str(row["realized_gross_pnl_usd"]))
                ef_d = Decimal(str(row["entry_fee_usd"]))
                xf_d = Decimal(str(row["exit_fee_usd"]))
                tf_d = Decimal(str(row["total_fees_usd"]))
                n_d = Decimal(str(row["realized_net_pnl_usd"]))
                if abs((ef_d + xf_d) - tf_d) > Decimal("0.0005"):
                    violations.append(f"H3_FEE_COMPONENT_SUM_DRIFT:line={ln_no}")
                res_d = abs((g_d - tf_d) - n_d)
                if res_d > max_resid:
                    max_resid = res_d
                if res_d > Decimal("0.001"):
                    violations.append(f"H3_GROSS_FEE_NET_RESIDUAL_GT_TOL:line={ln_no}:res={res_d}")
                closed_gross += g_d
                closed_fees += tf_d
                closed_net += n_d

            uids.append(str(row["episode_uid"]))
            rhashes.append(hashlib.sha256(ln.encode("utf-8")).hexdigest())

    if len(uids) != int(linked.get("total_episode_rows", -1)):
        violations.append("H3_LEDGER_ROW_COUNT_MISMATCH")
    if canonical_hash(uids) != linked.get("episode_uids_sha256"):
        violations.append("H3_LEDGER_UIDS_HASH_MISMATCH")
    if canonical_hash(rhashes) != linked.get("canonical_rows_sha256"):
        violations.append("H3_LEDGER_CANONICAL_ROWS_HASH_MISMATCH")

    leg = f09_doc.get("legacy_r1_filter_only_compatibility_summary") or {}
    if closed_cnt != int(leg.get("closed", -1)) or win_cnt != int(leg.get("wins", -1)) or loss_cnt != int(leg.get("losses", -1)):
        violations.append("H3_SUMMARY_WIN_LOSS_COUNT_MISMATCH")
    if abs(_dec4(closed_fees) - float(leg.get("total_fees_usd", -999.0))) > 0.002:
        violations.append("H3_SUMMARY_FEES_MISMATCH")
    if abs(_dec4(closed_net) - float(leg.get("net_pnl_usd", -999.0))) > 0.002:
        violations.append("H3_SUMMARY_NET_PNL_MISMATCH")

    ok = len(violations) == 0 and len(uids) > 0
    return {
        "valid": ok,
        "status": "PASS" if ok else "FAIL",
        "total_episode_rows": len(uids),
        "closed_count": closed_cnt,
        "max_residual_usd": _dec4(max_resid),
        "violations": violations,
    }


# ==============================================================================
# SECTION C — P0-SCI-03: STRICTLY FORWARD-ONLY EXPANDING DEV WALK-FORWARD
# ==============================================================================


def build_kh03_r2_wf_red_proofs(
    *,
    prior_r1_07_path: Path,
    prior_r1_15_path: Path,
    bars_1m: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """
    Produces Artifact `10_wf_old_posthoc_and_future_train_red_proofs.json`:
    Demonstrates objectively on R1 sealed artifacts 07 and 15:
      1) R1 `build_kh03_partition_manifest_and_noncontamination` used
         `close_time <= purge_lo_ms OR open_time >= purge_hi_ms`, placing FUTURE bars AFTER
         validation inside the training set (100% of Fold 1's 16,542 train bars were future!).
      2) R1 `run_kh03_full_research_study_10_to_16` merely sliced already-replayed full-sample
         episodes into 4 timestamp chunks without running independent fold-local chronological
         replays or purging training labels crossing the embargo boundary.
    """
    r1_07 = json.loads(prior_r1_07_path.read_text("utf-8"))
    r1_15 = json.loads(prior_r1_15_path.read_text("utf-8"))
    r1_folds = (r1_07.get("partitions") or {}).get("REUSED_DEV_VALIDATION_PURGED", {}).get("folds") or []

    fold_leak_audit: List[Dict[str, Any]] = []
    total_future_train_bars = 0
    for f in r1_folds:
        v_s = int(f["val_start_utc_ms"])
        v_e = int(f["val_end_utc_ms"])
        past_bars = sum(1 for b in bars_1m if int(b["close_time"]) <= v_s - MIN_EMBARGO_MS_KH03_R2)
        future_bars = sum(1 for b in bars_1m if int(b["open_time"]) >= v_e + MIN_EMBARGO_MS_KH03_R2)
        total_future_train_bars += future_bars
        fold_leak_audit.append(
            {
                "fold_id": f["fold_id"],
                "val_start_idx": int(f["val_start_idx"]),
                "val_end_idx": int(f["val_end_idx"]),
                "r1_reported_train_bars": int(f["train_bars_after_240m_embargo_count"]),
                "strictly_past_train_bars": past_bars,
                "future_after_val_train_bars_leaked": future_bars,
                "future_leakage_pct": round(100.0 * future_bars / max(1, past_bars + future_bars), 2),
            }
        )

    r1_h1_folds = ((r1_15.get("primary_arm_evaluations") or {}).get("KH03_H1_MTF_UNSWEPT_ALIGN") or {}).get("purged_wf_folds") or []
    r1_h1_sum_folds = sum(int(x.get("closed_trades", 0)) for x in r1_h1_folds)
    r1_h1_full_closed = int(((r1_15.get("primary_arm_evaluations") or {}).get("KH03_H1_MTF_UNSWEPT_ALIGN") or {}).get("closed_trades_1x", 0))

    ok = (
        len(fold_leak_audit) == 4
        and fold_leak_audit[0]["strictly_past_train_bars"] == 0
        and fold_leak_audit[0]["future_after_val_train_bars_leaked"] > 0
        and total_future_train_bars > 0
        and r1_h1_sum_folds == r1_h1_full_closed
    )
    out: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH03_R2,
        "schema_version": SCHEMA_VERSION_KH03_R2,
        "classification": "REAL_RED",
        "source_r1_07_sha256": _sha256_file(prior_r1_07_path),
        "source_r1_15_sha256": _sha256_file(prior_r1_15_path),
        "bug_1_location": "aurum-desk/backend/services/kh_03_data_governance_service.py:build_kh03_partition_manifest_and_noncontamination:478-482",
        "bug_2_location": "aurum-desk/backend/services/kh_03_research_service.py:run_kh03_full_research_study_10_to_16:851-868",
        "r1_future_train_leakage_by_fold": fold_leak_audit,
        "r1_total_future_train_bars_across_4_folds": total_future_train_bars,
        "r1_posthoc_slicing_proof": {
            "r1_h1_full_sample_closed_trades": r1_h1_full_closed,
            "r1_h1_sum_of_4_wf_slice_closed_trades": r1_h1_sum_folds,
            "exact_partition_of_single_full_sample_replay": (r1_h1_sum_folds == r1_h1_full_closed),
            "fold_local_validation_replay_performed_in_r1": False,
        },
        "status": "PASS" if ok else "FAIL",
    }
    out["artifact_canonical_sha256"] = canonical_hash({k: v for k, v in out.items() if k != "artifact_canonical_sha256"})
    return out


def build_kh03_r2_forward_only_dev_folds(
    *,
    bars_1m: Sequence[Mapping[str, Any]],
    mtf_records_by_idx: Optional[Mapping[int, Any]] = None,
    embargo_minutes: int = MIN_EMBARGO_MINUTES_KH03_R2,
    max_label_horizon_minutes: int = MAX_SIM_BAR_HORIZON_MINUTES_KH03_R2,
    n_blocks: int = 4,
    min_warmup_bars: int = 500,
    min_val_bars: int = 200,
) -> Dict[str, Any]:
    """
    Pure forward-only expanding DEV fold builder (P0-SCI-03).
    Block 0 is reserved strictly as initial past-only train + M15/H1 ATR warmup.
    Every validation fold `i` (`1 .. n_blocks-1`) enforces:
      - `train_start_ms <= train_end_ms < embargo_start_ms <= embargo_end_ms == val_start_ms <= val_end_ms`
      - `effective_embargo_minutes = max(embargo_minutes, max_label_horizon_minutes)` (>= 240 min)
      - Train bars ONLY satisfy `close_time <= val_start_ms - effective_embargo_ms` (NEVER post-val!)
      - `future_train_bars_count == 0` strictly.
    If history is too short to form >= 2 valid expanding folds, truthfully returns
    `WALKFORWARD_INSUFFICIENT_HISTORY_OR_POWER` (NEVER fabricates folds!).
    """
    eff_embargo_min = max(int(embargo_minutes), int(max_label_horizon_minutes))
    eff_embargo_ms = eff_embargo_min * 60_000
    n_bars = len(bars_1m)
    k_blocks = max(3, int(n_blocks))

    if n_bars < (min_warmup_bars + 2 * min_val_bars) or eff_embargo_min < MIN_EMBARGO_MINUTES_KH03_R2:
        return {
            "spec_version": SPEC_VERSION_KH03_R2,
            "schema_version": SCHEMA_VERSION_KH03_R2,
            "temporal_validation_kind": "FORWARD_ONLY_DEV_VALIDATION",
            "is_fresh_oos": False,
            "OOS_EVALUATIONS": 0,
            "embargo_minutes": eff_embargo_min,
            "max_label_horizon_minutes": int(max_label_horizon_minutes),
            "folds": [],
            "valid_folds_count": 0,
            "status": (
                "FAIL_EMBARGO_LT_240M"
                if eff_embargo_min < MIN_EMBARGO_MINUTES_KH03_R2
                else "WALKFORWARD_INSUFFICIENT_HISTORY_OR_POWER"
            ),
        }

    blk = n_bars // k_blocks
    folds: List[Dict[str, Any]] = []
    for fi in range(1, k_blocks):
        v_start_idx = fi * blk
        v_end_idx = (n_bars - 1) if fi == k_blocks - 1 else min(n_bars - 1, (fi + 1) * blk - 1)
        if v_end_idx - v_start_idx + 1 < min_val_bars:
            continue
        val_start_ms = int(bars_1m[v_start_idx]["open_time"])
        val_end_ms = int(bars_1m[v_end_idx]["close_time"])
        embargo_start_ms = val_start_ms - eff_embargo_ms

        train_idxs = [
            i for i in range(0, v_start_idx)
            if int(bars_1m[i]["close_time"]) <= embargo_start_ms
        ]
        if len(train_idxs) < min_warmup_bars:
            continue

        tr_start_idx = train_idxs[0]
        tr_end_idx = train_idxs[-1]
        tr_start_ms = int(bars_1m[tr_start_idx]["open_time"])
        tr_end_ms = int(bars_1m[tr_end_idx]["close_time"])
        embargo_bar_idxs = [
            i for i in range(tr_end_idx + 1, v_start_idx)
        ]
        # Verify strictly zero future train bars
        future_tr_bars = sum(1 for i in train_idxs if int(bars_1m[i]["open_time"]) >= val_start_ms)

        tr_ny_days = (
            len({str(mtf_records_by_idx[i].ny_date) for i in train_idxs if i in mtf_records_by_idx})
            if mtf_records_by_idx is not None
            else None
        )
        val_ny_days = (
            len({str(mtf_records_by_idx[i].ny_date) for i in range(v_start_idx, v_end_idx + 1) if i in mtf_records_by_idx})
            if mtf_records_by_idx is not None
            else None
        )

        folds.append(
            {
                "fold_id": f"R2_FWD_FOLD_{len(folds) + 1}",
                "role": "FORWARD_ONLY_DEV_VALIDATION",
                "is_fresh_oos": False,
                "train_start_idx": tr_start_idx,
                "train_end_idx": tr_end_idx,
                "train_bars_count": len(train_idxs),
                "train_min_ts_ms": tr_start_ms,
                "train_max_known_at_ts_ms": tr_end_ms,
                "train_ny_days": tr_ny_days,
                "future_train_bars_count": future_tr_bars,
                "embargo_start_ms": embargo_start_ms,
                "embargo_end_ms": val_start_ms,
                "embargo_minutes": eff_embargo_min,
                "embargo_buffer_bars_count": len(embargo_bar_idxs),
                "actual_train_to_val_gap_ms": val_start_ms - tr_end_ms,
                "val_start_idx": v_start_idx,
                "val_end_idx": v_end_idx,
                "val_bars_count": v_end_idx - v_start_idx + 1,
                "val_min_ts_ms": val_start_ms,
                "val_max_ts_ms": val_end_ms,
                "val_ny_days": val_ny_days,
            }
        )

    st = "PASS" if len(folds) >= 2 else "WALKFORWARD_INSUFFICIENT_HISTORY_OR_POWER"
    out = {
        "spec_version": SPEC_VERSION_KH03_R2,
        "schema_version": SCHEMA_VERSION_KH03_R2,
        "temporal_validation_kind": "FORWARD_ONLY_DEV_VALIDATION",
        "window_scheme": "EXPANDING_PAST_ONLY_TRAIN_NO_POST_VAL_BARS",
        "is_fresh_oos": False,
        "OOS_EVALUATIONS": 0,
        "total_m1_bars": n_bars,
        "embargo_minutes": eff_embargo_min,
        "max_label_horizon_minutes": int(max_label_horizon_minutes),
        "valid_folds_count": len(folds),
        "folds": folds,
        "status": st,
    }
    return out


def validate_kh03_r2_fold_causality(
    folds: Sequence[Mapping[str, Any]],
    *,
    min_embargo_minutes: int = MIN_EMBARGO_MINUTES_KH03_R2,
) -> Dict[str, Any]:
    """
    Fail-closed causality auditor for forward-only folds (used by both tests & independent verifier).
    Rejects:
      - `train_end >= val_start` or `train_start > train_end` or `val_start > val_end`
      - embargo `< min_embargo_minutes` (e.g. 239m59s fails!)
      - `actual_train_to_val_gap_ms < min_embargo_ms`
      - `future_train_bars_count > 0`
      - `train_last_label_exit_ts_ms > embargo_start_ms` (label crossing purge boundary!)
      - overlapping train/val episode IDs within a fold or duplicate validation episode IDs across folds
      - any fold claiming `is_fresh_oos == True`
      - non-monotonic validation fold ordering or overlapping validation intervals across folds
    """
    violations: List[str] = []
    min_emb_ms = int(min_embargo_minutes) * 60_000
    if not folds:
        return {"valid": False, "status": "FAIL", "violations": ["NO_FOLDS_PROVIDED"]}

    prev_val_end_ms = -1
    seen_val_ep_ids: Set[str] = set()

    for f in folds:
        fid = str(f.get("fold_id") or "UNKNOWN_FOLD")
        if bool(f.get("is_fresh_oos", True)):
            violations.append(f"FOLD_CLAIMED_FRESH_OOS:{fid}")
        tr_s = int(f.get("train_min_ts_ms", -1))
        tr_e = int(f.get("train_max_known_at_ts_ms", -1))
        emb_s = int(f.get("embargo_start_ms", -1))
        emb_e = int(f.get("embargo_end_ms", -1))
        val_s = int(f.get("val_min_ts_ms", -1))
        val_e = int(f.get("val_max_ts_ms", -1))
        emb_m = int(f.get("embargo_minutes", 0))

        if tr_s < 0 or tr_e < tr_s:
            violations.append(f"INVALID_TRAIN_INTERVAL:{fid}")
        if val_s < 0 or val_e < val_s:
            violations.append(f"INVALID_VAL_INTERVAL:{fid}")
        if tr_e >= val_s:
            violations.append(f"TRAIN_NOT_STRICTLY_BEFORE_VAL:{fid}:tr_e={tr_e}>=val_s={val_s}")
        if emb_m < int(min_embargo_minutes):
            violations.append(f"EMBARGO_MINUTES_BELOW_MIN:{fid}:{emb_m}<{min_embargo_minutes}")
        if (val_s - tr_e) < min_emb_ms:
            violations.append(f"ACTUAL_TRAIN_VAL_GAP_BELOW_EMBARGO:{fid}:gap_ms={val_s - tr_e}<{min_emb_ms}")
        if emb_e != val_s or (val_s - emb_s) < min_emb_ms:
            violations.append(f"EMBARGO_WINDOW_MISALIGNED:{fid}")
        if int(f.get("future_train_bars_count", 1)) != 0:
            violations.append(f"FUTURE_TRAIN_BARS_LEAKED:{fid}:cnt={f.get('future_train_bars_count')}")

        last_lbl_exit = f.get("train_last_label_exit_ts_ms")
        if last_lbl_exit is not None and int(last_lbl_exit) > emb_s:
            violations.append(f"TRAIN_LABEL_EXIT_CROSSED_EMBARGO:{fid}:exit={last_lbl_exit}>emb_s={emb_s}")

        if prev_val_end_ms >= 0 and val_s < prev_val_end_ms:
            violations.append(f"OVERLAPPING_OR_NON_MONOTONIC_VAL_FOLDS:{fid}:val_s={val_s}<prev_val_e={prev_val_end_ms}")
        prev_val_end_ms = val_e

        tr_ids = set(str(x) for x in (f.get("train_episode_ids") or ()))
        val_ids = set(str(x) for x in (f.get("val_episode_ids") or ()))
        overlap_tv = tr_ids & val_ids
        if overlap_tv:
            violations.append(f"TRAIN_VAL_EPISODE_OVERLAP:{fid}:cnt={len(overlap_tv)}")
        dup_val = seen_val_ep_ids & val_ids
        if dup_val:
            violations.append(f"CROSS_FOLD_DUPLICATE_VAL_EPISODE:{fid}:cnt={len(dup_val)}")
        seen_val_ep_ids.update(val_ids)

    ok = len(violations) == 0
    return {"valid": ok, "status": "PASS" if ok else "FAIL", "violations": violations}


def _replay_chronological_window_bounded(
    *,
    arm_code: str,
    window_id: str,
    candidates: Sequence[Mapping[str, Any]],
    bars_1m: Sequence[Mapping[str, Any]],
    window_start_idx: int,
    window_end_idx: int,
    hard_horizon_cutoff_ms: Optional[int] = None,
    spread_usd: float = DEFAULT_MODELED_SPREAD_USD,
    slippage_usd: float = DEFAULT_SLIPPAGE_USD,
    taker_fee_rate: float = 0.0004,
    maker_fee_rate: float = 0.0002,
    censor_label_at_window_end: str = "OPEN_AT_FOLD_END",
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Runs a BRAND NEW independent chronological 1-position replay STRICTLY inside
    `[window_start_idx .. window_end_idx]`.
    - Truncates `forward_bars` at `window_end_idx` so NO episode can read a single bar after
      `window_end_idx`.
    - If `hard_horizon_cutoff_ms` is set (for past-only training with embargo purging), ANY
      episode whose `label_exit_ts_ms` OR `horizon_end_ts_ms` exceeds `hard_horizon_cutoff_ms`
      is explicitly PURGED and returned in `purged_boundary_trades` (NEVER admitted to train!).
    """
    in_win = sorted(
        [
            c for c in candidates
            if window_start_idx <= int(c["checkpoint_index_1m"]) <= window_end_idx
        ],
        key=lambda c: (int(c["decision_timestamp_ms"]), str(c["direction"]), str(c["opportunity_id"])),
    )
    busy_until_ms = -1
    episodes: List[Dict[str, Any]] = []
    purged_trades: List[Dict[str, Any]] = []
    skipped_busy = 0
    overlap_violations = 0
    equity = Decimal("0")
    peak_eq = Decimal("0")
    max_dd = Decimal("0")

    for c in in_win:
        dec_ts = int(c["decision_timestamp_ms"])
        if dec_ts < busy_until_ms:
            skipped_busy += 1
            continue
        chk = int(c["checkpoint_index_1m"])
        fwd_end = min(window_end_idx + 1, chk + 1 + MAX_SIM_BAR_HORIZON_MINUTES_KH03_R2)
        fwd = list(bars_1m[chk + 1 : fwd_end])
        if not fwd:
            purged_trades.append(
                {
                    "opportunity_id": str(c["opportunity_id"]),
                    "decision_timestamp_ms": dec_ts,
                    "reason": "NO_FORWARD_BARS_WITHIN_WINDOW_BOUNDARY",
                }
            )
            continue

        horizon_end_ts = int(fwd[-1]["close_time"])
        led = simulate_v17931_trade_execution_net(
            variant=f"KH03_R2_WF_{window_id}_{arm_code}",
            episode_key=f"{window_id}_{arm_code}_{c['opportunity_id']}",
            setup_id=str(c.get("setup_episode_id") or c["opportunity_id"]),
            model_id="KH03_R2_FORWARD_ONLY_WF",
            direction=str(c["direction"]),
            decision_ts=dec_ts,
            checkpoint_index_1m=chk,
            planned_entry=float(c["entry_reference"]),
            planned_sl=float(c["selected_sl"]),
            planned_tp=float(c["take_profit"]),
            forward_bars=fwd,
            fill_mode="NEXT_BAR_OPEN_BID_ASK",
            spread_usd=spread_usd,
            slippage_usd=slippage_usd,
            taker_fee_rate=taker_fee_rate,
            maker_fee_rate=maker_fee_rate,
        )
        ld = led.to_dict()

        if not led.filled:
            outcome_label = str(led.exit_type)
            label_exit_ts = dec_ts
        elif led.exit_type in ("WIN", "LOSS", "LOSS_TIE_CONSERVATIVE", "ADVERSE_GAP_OPEN_STOP_LOSS"):
            outcome_label = "WIN" if led.exit_type == "WIN" else "LOSS"
            label_exit_ts = int(led.exit_fill_ts or (dec_ts + 60_000))
        elif led.exit_type == "OPEN_AT_HORIZON":
            outcome_label = censor_label_at_window_end if fwd_end == (window_end_idx + 1) else "OPEN_AT_HORIZON"
            label_exit_ts = horizon_end_ts
        else:
            outcome_label = "DATA_GAP_UNRESOLVED"
            label_exit_ts = int(led.trigger_bar_ts or fwd[0]["close_time"])

        # Strict embargo/label-horizon purging for training windows
        if hard_horizon_cutoff_ms is not None:
            if label_exit_ts > hard_horizon_cutoff_ms or outcome_label in ("OPEN_AT_HORIZON", censor_label_at_window_end):
                purged_trades.append(
                    {
                        "opportunity_id": str(c["opportunity_id"]),
                        "decision_timestamp_ms": dec_ts,
                        "label_exit_ts_ms": label_exit_ts,
                        "hard_horizon_cutoff_ms": hard_horizon_cutoff_ms,
                        "outcome_before_purge": outcome_label,
                        "reason": (
                            "PURGED_CENSORED_AT_TRAIN_CUTOFF"
                            if outcome_label in ("OPEN_AT_HORIZON", censor_label_at_window_end)
                            else "PURGED_LABEL_EXIT_CROSSED_EMBARGO_CUTOFF"
                        ),
                    }
                )
                if led.filled:
                    busy_until_ms = max(busy_until_ms, label_exit_ts)
                continue

        if episodes and led.filled and int(led.entry_fill_ts or dec_ts) < busy_until_ms:
            overlap_violations += 1
        if led.filled:
            busy_until_ms = max(busy_until_ms, label_exit_ts)

        norm_lbl_for_fee = "OPEN_AT_HORIZON" if outcome_label == censor_label_at_window_end else outcome_label
        fee_info = extract_authoritative_ledger_fees_kh03(
            ld, filled=bool(led.filled), outcome_label=norm_lbl_for_fee
        )
        net_d = Decimal(str(ld.get("realized_net_pnl_usd") or 0.0)) if outcome_label in ("WIN", "LOSS") else Decimal("0")
        if outcome_label in ("WIN", "LOSS"):
            equity += net_d
            if equity > peak_eq:
                peak_eq = equity
            dd = peak_eq - equity
            if dd > max_dd:
                max_dd = dd

        episodes.append(
            {
                "opportunity_id": str(c["opportunity_id"]),
                "setup_episode_id": str(c.get("setup_episode_id") or c["opportunity_id"]),
                "direction": str(c["direction"]),
                "decision_timestamp_ms": dec_ts,
                "label_exit_ts_ms": label_exit_ts,
                "ny_date": str(c.get("ny_date") or "UNK"),
                "filled": bool(led.filled),
                "outcome_label": outcome_label,
                "exit_type": str(led.exit_type),
                "executed_quantity": round(float(led.quantity or 0.0), 4),
                "planned_net_risk_usd": round(float(led.planned_net_risk_usd or 0.0), 4),
                "realized_gross_pnl_usd": round(float(ld.get("realized_gross_pnl_usd") or 0.0), 4),
                "entry_fee_usd": fee_info["entry_fee_usd"],
                "exit_fee_usd": fee_info["exit_fee_usd"],
                "total_fees_usd": fee_info["total_fees_usd"],
                "accrued_open_entry_fee_usd": fee_info["accrued_open_entry_fee_usd"],
                "gross_fee_net_residual_usd": fee_info["gross_fee_net_residual_usd"],
                "realized_net_pnl_usd": round(float(ld.get("realized_net_pnl_usd") or 0.0), 4),
                "realized_net_r": round(
                    float(
                        ld.get("realized_r_net")
                        if ld.get("realized_r_net") is not None
                        else (
                            ld.get("realized_r")
                            if ld.get("realized_r") is not None
                            else (float(ld.get("realized_net_pnl_usd") or 0.0) / max(0.01, float(led.planned_net_risk_usd or 2.50)))
                        )
                    ),
                    4,
                ),
            }
        )

    summary = _summarize_episodes(episodes)
    summary["open_at_fold_end"] = sum(1 for e in episodes if e["outcome_label"] == censor_label_at_window_end)
    summary["max_drawdown_usd"] = _dec4(max_dd)
    loss_abs = [abs(float(e["realized_net_pnl_usd"])) for e in episodes if e["outcome_label"] == "LOSS"]
    summary["max_realized_loss_usd"] = round(max(loss_abs), 4) if loss_abs else 0.0
    summary["eligible_candidates_in_window"] = len(in_win)
    summary["skipped_due_to_active_position"] = skipped_busy
    summary["purged_boundary_trades_count"] = len(purged_trades)
    summary["overlap_violations"] = overlap_violations
    summary["executed_ids_sha256"] = canonical_hash([e["opportunity_id"] for e in episodes])
    return summary, episodes, purged_trades


def select_past_only_arm_for_fold(
    *,
    fold_id: str,
    tested_code_sha: str,
    train_arm_summaries: Mapping[str, Mapping[str, Any]],
    candidate_arms: Sequence[str] = KH03_R2_EXECUTABLE_PRIMARY_ARMS,
) -> Dict[str, Any]:
    """
    Deterministic past-only fold-local selector (FREEZES BEFORE validation replay).
    Selects exclusively among `candidate_arms` using strictly past purged train expectancy_r
    (tie-break by alphabetical arm_code). If no arm has positive train expectancy, records
    `selected_arm_meets_positive_train_bar = False` while still freezing the highest-ranked
    past-only arm deterministically so validation evaluates zero peeking.
    """
    scored: List[Tuple[float, str, Dict[str, Any]]] = []
    train_scores: Dict[str, Any] = {}
    for ac in candidate_arms:
        s = train_arm_summaries.get(ac) or {}
        cl = int(s.get("closed") or 0)
        exp_r = float(s["expectancy_r"]) if (cl > 0 and s.get("expectancy_r") is not None) else -999.0
        train_scores[ac] = {
            "closed_train_trades": cl,
            "train_expectancy_r": s.get("expectancy_r"),
            "train_net_pnl_usd": s.get("net_pnl_usd"),
            "train_executed_ids_sha256": s.get("executed_ids_sha256"),
        }
        scored.append((-exp_r, ac, train_scores[ac]))

    scored.sort(key=lambda t: (t[0], t[1]))
    chosen_arm = scored[0][1] if scored else candidate_arms[0]
    chosen_exp = train_scores[chosen_arm]["train_expectancy_r"]
    meets_pos = chosen_exp is not None and float(chosen_exp) > 0.0

    rec: Dict[str, Any] = {
        "fold_id": fold_id,
        "tested_code_sha": tested_code_sha,
        "candidate_arms_pool": list(candidate_arms),
        "train_only_arm_scores": train_scores,
        "selected_arm_on_past_train_only": chosen_arm,
        "selected_arm_meets_positive_train_bar": meets_pos,
        "selection_frozen_before_validation_replay": True,
    }
    rec["selection_freeze_sha256"] = canonical_hash(
        {k: v for k, v in rec.items() if k != "selection_freeze_sha256"}
    )
    return rec


def run_kh03_r2_forward_only_walkforward(
    *,
    datasets: Mapping[str, Any],
    mtf_bundle: Mapping[str, Any],
    r2_cands_1x: Mapping[str, Any],
    tested_code_sha: str,
    prior_r1_15_doc: Optional[Mapping[str, Any]] = None,
    seed: int = DEFAULT_RANDOM_SEED_KH02_R2,
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """
    Executes true expanding forward-only DEV temporal validation (P0-SCI-03), producing:
      - `11_wf_forward_only_folds_train_val_timestamps.json`
      - `12_wf_purged_labels_embargo_episode_nonoverlap.json`
      - `13_wf_fold_local_selection_freeze_and_replay.json`
    """
    bars_1m = list(datasets["1m"])
    rec_by_idx = _mtf_records_by_idx(mtf_bundle)

    kh03_cands_1x = build_kh03_arm_candidates_from_r2_universe(
        datasets=datasets, mtf_bundle=mtf_bundle, r2_cands_bundle=r2_cands_1x,
        spread_usd=DEFAULT_MODELED_SPREAD_USD, slippage_usd=DEFAULT_SLIPPAGE_USD, seed=seed,
    )

    fold_spec = build_kh03_r2_forward_only_dev_folds(
        bars_1m=bars_1m,
        mtf_records_by_idx=rec_by_idx,
        embargo_minutes=MIN_EMBARGO_MINUTES_KH03_R2,
        max_label_horizon_minutes=MAX_SIM_BAR_HORIZON_MINUTES_KH03_R2,
        n_blocks=4,
    )
    folds_raw = list(fold_spec["folds"])

    enriched_folds_11: List[Dict[str, Any]] = []
    purge_audit_12: List[Dict[str, Any]] = []
    fold_replays_13: List[Dict[str, Any]] = []

    arm_fold_exps: Dict[str, List[float]] = {ac: [] for ac in KH03_ALL_ARMS}
    arm_stitched_eps: Dict[str, List[Dict[str, Any]]] = {ac: [] for ac in KH03_ALL_ARMS}
    selected_stitched_eps: List[Dict[str, Any]] = []
    total_wf_overlaps = 0

    for f in folds_raw:
        fid = str(f["fold_id"])
        tr_s_i = int(f["train_start_idx"])
        tr_e_i = int(f["train_end_idx"])
        val_s_i = int(f["val_start_idx"])
        val_e_i = int(f["val_end_idx"])
        emb_s_ms = int(f["embargo_start_ms"])

        # Step 1: Run strictly past purged train replay for executable primary arms
        train_arm_sums: Dict[str, Dict[str, Any]] = {}
        fold_purged_all: List[Dict[str, Any]] = []
        fold_train_ep_ids: List[str] = []
        train_last_exit_ms: Optional[int] = None

        for ac in KH03_R2_EXECUTABLE_PRIMARY_ARMS:
            tsum, teps, tpurged = _replay_chronological_window_bounded(
                arm_code=ac,
                window_id=f"{fid}_TRAIN",
                candidates=kh03_cands_1x["arm_candidates"][ac],
                bars_1m=bars_1m,
                window_start_idx=tr_s_i,
                window_end_idx=tr_e_i,
                hard_horizon_cutoff_ms=emb_s_ms,
            )
            total_wf_overlaps += int(tsum.get("overlap_violations", 0))
            train_arm_sums[ac] = tsum
            for tp in tpurged:
                fold_purged_all.append({"arm_code": ac, **tp})
            for te in teps:
                fold_train_ep_ids.append(f"{ac}:{te['opportunity_id']}")
                lex = int(te["label_exit_ts_ms"])
                if train_last_exit_ms is None or lex > train_last_exit_ms:
                    train_last_exit_ms = lex

        # Step 2: Freeze past-only fold-local selection BEFORE touching validation window!
        sel_freeze = select_past_only_arm_for_fold(
            fold_id=fid,
            tested_code_sha=tested_code_sha,
            train_arm_summaries=train_arm_sums,
            candidate_arms=KH03_R2_EXECUTABLE_PRIMARY_ARMS,
        )

        # Step 3: Fresh independent fold-local chronological 1-position validation replay
        val_arm_results: Dict[str, Dict[str, Any]] = {}
        fold_val_ep_ids_null: List[str] = []
        fold_val_all_ids: List[str] = []
        for ac in KH03_ALL_ARMS:
            vsum, veps, vpurged = _replay_chronological_window_bounded(
                arm_code=ac,
                window_id=f"{fid}_VAL",
                candidates=kh03_cands_1x["arm_candidates"][ac],
                bars_1m=bars_1m,
                window_start_idx=val_s_i,
                window_end_idx=val_e_i,
                hard_horizon_cutoff_ms=None,
                censor_label_at_window_end="OPEN_AT_FOLD_END",
            )
            total_wf_overlaps += int(vsum.get("overlap_violations", 0))
            cl_eps = [e for e in veps if e["filled"] and e["outcome_label"] in ("WIN", "LOSS")]
            r_vals = [float(e["realized_net_r"]) for e in cl_eps]
            ny_days = [str(e.get("ny_date") or "UNK") for e in cl_eps]
            ci_raw = _block_bootstrap_ci(r_vals, ny_days, seed=seed + 911) if r_vals else None
            ci_p = parse_kh03_cluster_ci_strict(ci_raw, label=f"{fid}:{ac}")
            if vsum.get("expectancy_r") is not None and int(vsum.get("closed") or 0) > 0:
                arm_fold_exps[ac].append(float(vsum["expectancy_r"]))
            arm_stitched_eps[ac].extend(veps)

            if ac == "NC_NULL_TRIGGER":
                fold_val_ep_ids_null = [str(e["opportunity_id"]) for e in veps]
            for ve in veps:
                fold_val_all_ids.append(f"{ac}:{ve['opportunity_id']}")

            v_status = (
                "INCONCLUSIVE_LOW_FOLD_N"
                if int(vsum.get("closed") or 0) < 15
                else "EVALUATED_REUSED_DEV_FOLD"
            )
            val_arm_results[ac] = {
                "arm_role": (
                    "R1_FILTER_ONLY_COMPATIBILITY_DISQUALIFIED_FROM_EXECUTABLE"
                    if ac == KH03_R2_H3_ADVISORY_ARM
                    else ("PRIMARY_STATIC_PRE_REGISTERED" if ac in KH03_R2_EXECUTABLE_PRIMARY_ARMS else "NEGATIVE_CONTROL")
                ),
                "validation_mode": "FORWARD_TEMPORAL_HOLDOUT_EVALUATION_ON_REUSED_DEV",
                "summary": vsum,
                "ny_day_cluster_95ci_raw": ci_raw,
                "ny_day_cluster_95ci_parsed": ci_p,
                "fold_sample_status": v_status,
                "val_boundary_excluded_count": len(vpurged),
            }

            if ac == sel_freeze["selected_arm_on_past_train_only"]:
                selected_stitched_eps.extend(veps)

        # Enrich fold record with exact episode ID hashes & label exit timestamp
        f_enr = dict(f)
        f_enr["train_last_label_exit_ts_ms"] = train_last_exit_ms
        f_enr["train_episode_ids_sha256"] = canonical_hash(sorted(fold_train_ep_ids))
        f_enr["val_episode_ids_sha256"] = canonical_hash(sorted(fold_val_all_ids))
        f_enr["train_episode_ids"] = sorted(fold_train_ep_ids)
        f_enr["val_episode_ids"] = sorted(fold_val_all_ids)
        f_enr["purged_train_boundary_trades_count"] = len(fold_purged_all)
        enriched_folds_11.append(f_enr)

        purge_audit_12.append(
            {
                "fold_id": fid,
                "embargo_start_ms": emb_s_ms,
                "embargo_end_ms": int(f["embargo_end_ms"]),
                "train_last_label_exit_ts_ms": train_last_exit_ms,
                "train_last_exit_strictly_le_embargo_start": (
                    train_last_exit_ms is None or int(train_last_exit_ms) <= emb_s_ms
                ),
                "purged_boundary_trades_count": len(fold_purged_all),
                "purged_boundary_trades": fold_purged_all[:25],
                "train_val_episode_overlap_count": len(set(fold_train_ep_ids) & set(fold_val_all_ids)),
                "val_null_unique_opportunities_count": len(set(fold_val_ep_ids_null)),
            }
        )

        fold_replays_13.append(
            {
                "fold_id": fid,
                "train_interval_utc_ms": [int(f["train_min_ts_ms"]), int(f["train_max_known_at_ts_ms"])],
                "embargo_interval_utc_ms": [emb_s_ms, int(f["embargo_end_ms"])],
                "val_interval_utc_ms": [int(f["val_min_ts_ms"]), int(f["val_max_ts_ms"])],
                "fold_local_selection_freeze": sel_freeze,
                "selected_arm_validation_result": val_arm_results[sel_freeze["selected_arm_on_past_train_only"]],
                "static_preregistered_arm_validation_results": val_arm_results,
            }
        )

    causality_check = validate_kh03_r2_fold_causality(
        enriched_folds_11, min_embargo_minutes=MIN_EMBARGO_MINUTES_KH03_R2
    )

    # Future-injection invariance check: injecting huge +100R wins AFTER val_start_ms MUST NOT alter Fold 1 selection!
    future_inj_ok = False
    if folds_raw:
        f1 = folds_raw[0]
        # Construct fake future-only candidates inside validation window with huge profits; past train window stays identical
        fake_future_cands = list(kh03_cands_1x["arm_candidates"]["KH03_H2_LOW_FRICTION_VOL_REGIME"])
        fake_future_cands.append(
            {
                "opportunity_id": "FAKE_FUTURE_HUGE_WIN_POST_VAL",
                "checkpoint_index_1m": int(f1["val_start_idx"]) + 50,
                "decision_timestamp_ms": int(bars_1m[int(f1["val_start_idx"]) + 50]["close_time"]),
                "direction": "LONG",
                "entry_reference": 4000.0,
                "selected_sl": 3999.0,
                "take_profit": 4100.0,
                "ny_date": "2026-10-20",
            }
        )
        t_h2_orig, _, _ = _replay_chronological_window_bounded(
            arm_code="KH03_H2_LOW_FRICTION_VOL_REGIME",
            window_id="FINJ_ORIG",
            candidates=kh03_cands_1x["arm_candidates"]["KH03_H2_LOW_FRICTION_VOL_REGIME"],
            bars_1m=bars_1m,
            window_start_idx=int(f1["train_start_idx"]),
            window_end_idx=int(f1["train_end_idx"]),
            hard_horizon_cutoff_ms=int(f1["embargo_start_ms"]),
        )
        t_h2_inj, _, _ = _replay_chronological_window_bounded(
            arm_code="KH03_H2_LOW_FRICTION_VOL_REGIME",
            window_id="FINJ_MUT",
            candidates=fake_future_cands,
            bars_1m=bars_1m,
            window_start_idx=int(f1["train_start_idx"]),
            window_end_idx=int(f1["train_end_idx"]),
            hard_horizon_cutoff_ms=int(f1["embargo_start_ms"]),
        )
        sel_inj = select_past_only_arm_for_fold(
            fold_id=str(f1["fold_id"]),
            tested_code_sha=tested_code_sha,
            train_arm_summaries={
                "KH03_H1_MTF_UNSWEPT_ALIGN": fold_replays_13[0]["fold_local_selection_freeze"]["train_only_arm_scores"]["KH03_H1_MTF_UNSWEPT_ALIGN"]
                and {
                    "closed": fold_replays_13[0]["fold_local_selection_freeze"]["train_only_arm_scores"]["KH03_H1_MTF_UNSWEPT_ALIGN"]["closed_train_trades"],
                    "expectancy_r": fold_replays_13[0]["fold_local_selection_freeze"]["train_only_arm_scores"]["KH03_H1_MTF_UNSWEPT_ALIGN"]["train_expectancy_r"],
                    "net_pnl_usd": fold_replays_13[0]["fold_local_selection_freeze"]["train_only_arm_scores"]["KH03_H1_MTF_UNSWEPT_ALIGN"]["train_net_pnl_usd"],
                    "executed_ids_sha256": fold_replays_13[0]["fold_local_selection_freeze"]["train_only_arm_scores"]["KH03_H1_MTF_UNSWEPT_ALIGN"]["train_executed_ids_sha256"],
                },
                "KH03_H2_LOW_FRICTION_VOL_REGIME": t_h2_inj,
            },
        )
        future_inj_ok = (
            t_h2_orig["executed_ids_sha256"] == t_h2_inj["executed_ids_sha256"]
            and sel_inj["selection_freeze_sha256"] == fold_replays_13[0]["fold_local_selection_freeze"]["selection_freeze_sha256"]
        )

    # Stitched non-overlapping validation summary per arm (deduplicated strictly by opportunity_id)
    stitched_by_arm: Dict[str, Any] = {}
    r1_vs_r2_comparison: Dict[str, Any] = {}
    r1_evals = (prior_r1_15_doc or {}).get("primary_arm_evaluations") or {}
    for ac in KH03_ALL_ARMS:
        eps_all = arm_stitched_eps[ac]
        unique_ids = [e["opportunity_id"] for e in eps_all]
        dup_cnt = len(unique_ids) - len(set(unique_ids))
        st_sum = _summarize_episodes(eps_all)
        exps = sorted(arm_fold_exps[ac])
        med_e = exps[len(exps) // 2] if exps else None
        pos_r = round(sum(1 for x in exps if x > 0.0) / max(1, len(exps)), 4) if exps else 0.0
        stitched_by_arm[ac] = {
            "stitched_validation_folds_count": len(folds_raw),
            "folds_with_closed_trades_count": len(exps),
            "duplicate_opportunity_ids_across_folds": dup_cnt,
            "stitched_summary": st_sum,
            "median_fold_expectancy_r": med_e,
            "positive_fold_ratio": pos_r,
            "per_fold_expectancy_r": arm_fold_exps[ac],
        }
        if ac in KH03_PRIMARY_ARMS:
            r1_st = r1_evals.get(ac) or {}
            r1_vs_r2_comparison[ac] = {
                "disclosure": "METHODOLOGY_CHANGE_NOT_STRATEGY_IMPROVEMENT",
                "r1_posthoc_4_slices_closed_trades": r1_st.get("closed_trades_1x"),
                "r1_posthoc_4_slices_median_fold_exp_r": r1_st.get("purged_wf_median_fold_expectancy_r"),
                "r2_forward_only_3_folds_stitched_closed_trades": st_sum.get("closed"),
                "r2_forward_only_3_folds_median_fold_exp_r": med_e,
                "r2_forward_only_3_folds_stitched_net_pnl_usd": st_sum.get("net_pnl_usd"),
                "r2_excludes_block0_initial_train_warmup_from_validation": True,
            }

    sel_stitched_sum = _summarize_episodes(selected_stitched_eps)
    sel_dup_cnt = len(selected_stitched_eps) - len({e["opportunity_id"] for e in selected_stitched_eps})

    # Strip full episode ID lists from compact 11 summary AFTER causality check (keep hashes & counts + explicit verification)
    folds_11_compact = []
    for fe in enriched_folds_11:
        fc = {k: v for k, v in fe.items() if k not in ("train_episode_ids", "val_episode_ids")}
        fc["train_episodes_count"] = len(fe["train_episode_ids"])
        fc["val_episodes_count"] = len(fe["val_episode_ids"])
        folds_11_compact.append(fc)

    f11: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH03_R2,
        "schema_version": SCHEMA_VERSION_KH03_R2,
        "temporal_validation_kind": "FORWARD_ONLY_DEV_VALIDATION",
        "window_scheme": "EXPANDING_PAST_ONLY_TRAIN_NO_POST_VAL_BARS",
        "is_fresh_oos": False,
        "OOS_EVALUATIONS": 0,
        "FRESH_OOS_VERDICT": "BLOCKED_NO_UNTOUCHED_DATA",
        "embargo_minutes": int(fold_spec["embargo_minutes"]),
        "max_label_horizon_minutes": int(fold_spec["max_label_horizon_minutes"]),
        "valid_folds_count": len(folds_11_compact),
        "folds": folds_11_compact,
        "causality_audit": causality_check,
        "status": "PASS" if (fold_spec["status"] == "PASS" and causality_check["valid"]) else "FAIL",
    }
    f11["artifact_canonical_sha256"] = canonical_hash({k: v for k, v in f11.items() if k != "artifact_canonical_sha256"})

    no_dup_across_folds = all(v["duplicate_opportunity_ids_across_folds"] == 0 for v in stitched_by_arm.values()) and sel_dup_cnt == 0
    f12: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH03_R2,
        "schema_version": SCHEMA_VERSION_KH03_R2,
        "embargo_minutes": int(fold_spec["embargo_minutes"]),
        "max_label_horizon_minutes": int(fold_spec["max_label_horizon_minutes"]),
        "all_train_exits_strictly_le_embargo_start": all(p["train_last_exit_strictly_le_embargo_start"] for p in purge_audit_12),
        "all_train_val_episode_overlaps_zero": all(p["train_val_episode_overlap_count"] == 0 for p in purge_audit_12),
        "all_cross_fold_val_duplicates_zero": no_dup_across_folds,
        "total_purged_train_boundary_trades": sum(p["purged_boundary_trades_count"] for p in purge_audit_12),
        "total_overlap_violations_across_wf_replays": total_wf_overlaps,
        "per_fold_purge_audit": purge_audit_12,
        "status": (
            "PASS"
            if (
                causality_check["valid"]
                and no_dup_across_folds
                and total_wf_overlaps == 0
                and all(p["train_last_exit_strictly_le_embargo_start"] for p in purge_audit_12)
            )
            else "FAIL"
        ),
    }
    f12["artifact_canonical_sha256"] = canonical_hash({k: v for k, v in f12.items() if k != "artifact_canonical_sha256"})

    f13: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH03_R2,
        "schema_version": SCHEMA_VERSION_KH03_R2,
        "temporal_validation_kind": "FORWARD_ONLY_DEV_VALIDATION",
        "static_arms_mode_disclosure": (
            "Static pre-registered arms (KH03_H1, KH03_H2, controls) are evaluated via "
            "`FORWARD_TEMPORAL_HOLDOUT_EVALUATION_ON_REUSED_DEV` (no model retraining claimed). "
            "Fold-local arm selection is separately evaluated using strictly past purged train windows "
            "with selection SHA frozen BEFORE each validation replay."
        ),
        "reused_dev_not_fresh_oos_disclosure": (
            "Entire historical DEV dataset was previously inspected in KH-02/KH-03.R1. R2 forward-only "
            "folds measure temporal robustness on REUSED DEV only and NEVER constitute fresh OOS."
        ),
        "future_win_injection_invariance_passed": future_inj_ok,
        "per_fold_selection_and_validation_replays": fold_replays_13,
        "stitched_non_overlapping_validation_by_arm": stitched_by_arm,
        "stitched_fold_local_selected_portfolio": {
            "duplicate_opportunity_ids": sel_dup_cnt,
            "summary": sel_stitched_sum,
            "selection_bias_warning": "Evaluated on reused DEV historical data; negative realized return; never proof of edge.",
        },
        "r1_posthoc_vs_r2_forward_only_comparison": r1_vs_r2_comparison,
        "status": "PASS" if (causality_check["valid"] and future_inj_ok and total_wf_overlaps == 0 and no_dup_across_folds) else "FAIL",
    }
    f13["artifact_canonical_sha256"] = canonical_hash({k: v for k, v in f13.items() if k != "artifact_canonical_sha256"})
    return f11, f12, f13


# ==============================================================================
# SECTION D — RECOMPUTED R2 STATISTICAL VERDICTS, 6-COST & RISK TAIL (14, 15, 16, 23)
# ==============================================================================


def run_kh03_r2_recomputed_science_14_16_23(
    *,
    datasets: Mapping[str, Any],
    mtf_bundle: Mapping[str, Any],
    ledger_bundle: Mapping[str, Any],
    r2_cands_1x: Mapping[str, Any],
    h3_policy_07: Mapping[str, Any],
    h3_disclosure_09: Mapping[str, Any],
    wf_11: Mapping[str, Any],
    wf_13: Mapping[str, Any],
    tested_code_sha: str,
    seed: int = DEFAULT_RANDOM_SEED_KH02_R2,
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """
    Recomputes Artifacts `14`, `15`, `16`, and `23` using ALL THREE KH-03.R2 methodology closures:
      - P0-SCI-01 strict CI parser & raw-evidence DEV gate
      - P0-SCI-02 PATH B H3 disqualification from `EXECUTABLE_EDGE_CANDIDATE`
      - P0-SCI-03 genuine forward-only expanding DEV walk-forward fold metrics
      - Full 6-scenario dynamic re-risked cost stress (1X / FEE_ONLY / 2X / 3X) with R1 Decimal fee root fix
      - Honest risk-tail disclosure (`planned_cap_is_physical_guarantee = False`)
      - Orthogonal verdicts (`NO_ECONOMIC_ADVANCEMENT_CANDIDATE`, `NO_PROVEN_EDGE`,
        `BLOCKED_NO_UNTOUCHED_DATA`, `BLOCKED_NO_DEV_CANDIDATE`, `ACTIVATION_READY=NO`, `OOS_EVALUATIONS=0`).
    """
    bars_1m = list(datasets["1m"])
    rec_by_idx = _mtf_records_by_idx(mtf_bundle)

    kh03_cands_1x = build_kh03_arm_candidates_from_r2_universe(
        datasets=datasets, mtf_bundle=mtf_bundle, r2_cands_bundle=r2_cands_1x,
        spread_usd=DEFAULT_MODELED_SPREAD_USD, slippage_usd=DEFAULT_SLIPPAGE_USD, seed=seed,
    )

    base_replays_1x: Dict[str, Dict[str, Any]] = {}
    base_episodes_1x: Dict[str, List[Dict[str, Any]]] = {}
    total_ov_1x = 0
    for ac in KH03_ALL_ARMS:
        rs, eps = _replay_chronological_custom_cost_scenario(
            arm_code=ac,
            scenario_id="1X_BASE_0p0004",
            candidates=kh03_cands_1x["arm_candidates"][ac],
            bars_1m=bars_1m,
            spread_usd=DEFAULT_MODELED_SPREAD_USD,
            slippage_usd=DEFAULT_SLIPPAGE_USD,
            taker_fee_rate=0.0004,
            maker_fee_rate=0.0002,
            return_episodes=True,
        )
        total_ov_1x += int(rs.get("overlap_violations", 0))
        base_replays_1x[ac] = rs
        base_episodes_1x[ac] = eps

    # 15: Full 6-scenario dynamic re-risked cost stress & fee integrity
    six_scenarios: Dict[str, Any] = {}
    total_ov_6 = 0
    zero_fee_bugs_15 = 0
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
                total_ov_6 += int(rep_s.get("overlap_violations", 0))
                sc_arms_rep[ac] = rep_s

        for ac, rs in sc_arms_rep.items():
            g_d = Decimal(str(rs.get("gross_pnl_usd") or 0.0))
            f_d = Decimal(str(rs.get("total_fees_usd") or 0.0))
            n_d = Decimal(str(rs.get("net_pnl_usd") or 0.0))
            if int(rs.get("closed") or 0) > 0 and f_d == Decimal("0") and (g_d - n_d) > Decimal("0.01"):
                zero_fee_bugs_15 += 1

        six_scenarios[sc_id] = {
            "parameters": {"spread_usd": sp_usd, "slippage_usd": slp_usd, "taker_fee_rate": taker_r, "maker_fee_rate": maker_r},
            "population_mode": "DYNAMIC_FULL_UNIVERSE_RE_RISKED",
            "raw_r2_eligible_denominator": sc_cands["raw_r2_geometry_risk_eligible_count"],
            "arms": sc_arms_rep,
        }

    f15: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH03_R2,
        "schema_version": SCHEMA_VERSION_KH03_R2,
        "data_regime": "REUSED_DEV_NOT_FRESH_OOS",
        "quote_mode": "MODELED_BID_ASK",
        "observed_l1_bbo_available": False,
        "ACCOUNT_EFFECTIVE_FEE_VERDICT": "UNKNOWN_NOT_LOCALLY_VERIFIED",
        "h3_cost_stress_disclosure": "KH03_H3 rows reflect R1_FILTER_ONLY_COMPATIBILITY (PATH B; size_applied_to_execution=False), NOT executable resize.",
        "total_overlap_violations": total_ov_6,
        "zero_fee_display_bugs_count": zero_fee_bugs_15,
        "scenarios": six_scenarios,
        "status": "PASS" if (total_ov_6 == 0 and zero_fee_bugs_15 == 0) else "FAIL",
    }
    f15["artifact_canonical_sha256"] = canonical_hash({k: v for k, v in f15.items() if k != "artifact_canonical_sha256"})

    # Stratified common-support matching vs NC_NULL_TRIGGER (with §07 honesty disclosure)
    m1_mid_ts = int(bars_1m[len(bars_1m) // 2]["close_time"]) if bars_1m else 0
    null_closed = [e for e in base_episodes_1x["NC_NULL_TRIGGER"] if e["filled"] and e["outcome_label"] in ("WIN", "LOSS")]

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
            "matched_pairs_count": eff_n,
            "overlap_coverage_ratio": overlap_cov,
            "max_post_abs_smd": max_post,
            "matching_quality_status": mq,
            "inadequate_reasons": inad,
            "causal_comparison_eligible_only_not_proof": (mq == "BALANCED_COMMON_SUPPORT"),
            "matched_mean_delta_r_vs_null": mean_delta,
            "matched_ny_day_95ci_delta_r": ci_delta,
            "matched_ny_day_95ci_delta_parsed": parse_kh03_cluster_ci_strict(ci_delta, label=f"{ac}:matched_delta"),
            "expectancy_r_vs_placebo_shuffled": (
                round(float(base_replays_1x[ac]["expectancy_r"]) - float(base_replays_1x["NC_PLACEBO_SHUFFLED_TRIGGER"]["expectancy_r"]), 4)
                if (base_replays_1x[ac].get("expectancy_r") is not None and base_replays_1x["NC_PLACEBO_SHUFFLED_TRIGGER"].get("expectancy_r") is not None)
                else None
            ),
            "expectancy_r_vs_time_offset": (
                round(float(base_replays_1x[ac]["expectancy_r"]) - float(base_replays_1x["NC_TIME_OFFSET_SHIFT"]["expectancy_r"]), 4)
                if (base_replays_1x[ac].get("expectancy_r") is not None and base_replays_1x["NC_TIME_OFFSET_SHIFT"].get("expectancy_r") is not None)
                else None
            ),
        }

    # 14: Recomputed Holm-Bonferroni (m=3 preserved!), strict CI parsing & forward-only WF integration
    raw_pvals: Dict[str, Optional[float]] = {}
    raw_arm_metrics: Dict[str, Dict[str, Any]] = {}
    stitched_wf = wf_13.get("stitched_non_overlapping_validation_by_arm") or {}
    valid_wf_folds_n = int(wf_11.get("valid_folds_count") or 0)

    for ac in KH03_PRIMARY_ARMS:
        cl_eps = [e for e in base_episodes_1x[ac] if e["filled"] and e["outcome_label"] in ("WIN", "LOSS")]
        r_vals = [float(e["realized_net_r"]) for e in cl_eps]
        ny_days = [str(e.get("ny_date") or "UNK") for e in cl_eps]
        ep_ids = [str(e.get("setup_episode_id") or e["opportunity_id"]) for e in cl_eps]
        pval = _one_sided_cluster_bootstrap_pvalue_gt_zero(r_vals, ny_days, seed=seed + 701 + KH03_PRIMARY_ARMS.index(ac))
        raw_pvals[ac] = pval
        ci_ny_raw = _block_bootstrap_ci(r_vals, ny_days, seed=seed + 11) if r_vals else None
        ci_ep_raw = _block_bootstrap_ci(r_vals, ep_ids, seed=seed + 29) if r_vals else None
        wf_ac = stitched_wf.get(ac) or {}
        raw_arm_metrics[ac] = {
            "closed_trades_1x": len(cl_eps),
            "wins_1x": sum(1 for e in cl_eps if e["outcome_label"] == "WIN"),
            "losses_1x": sum(1 for e in cl_eps if e["outcome_label"] == "LOSS"),
            "win_rate_1x": round(sum(1 for e in cl_eps if e["outcome_label"] == "WIN") / max(1, len(cl_eps)), 4) if cl_eps else 0.0,
            "effective_ny_days_1x": len(set(ny_days)),
            "expectancy_r_1x": base_replays_1x[ac].get("expectancy_r"),
            "net_pnl_usd_1x": base_replays_1x[ac].get("net_pnl_usd"),
            "total_fees_usd_1x": base_replays_1x[ac].get("total_fees_usd"),
            "expectancy_r_fee_only_0p0006": six_scenarios["FEE_ONLY_0p0006"]["arms"][ac].get("expectancy_r"),
            "expectancy_r_2x": six_scenarios["2X_ALL_COST_0p0004"]["arms"][ac].get("expectancy_r"),
            "expectancy_r_3x": six_scenarios["3X_ALL_COST_0p0004"]["arms"][ac].get("expectancy_r"),
            "ny_day_cluster_95ci_r": ci_ny_raw,
            "episode_cluster_95ci_r": ci_ep_raw,
            "raw_one_sided_pvalue_gt_zero": pval,
            "forward_only_wf_valid_folds_count": valid_wf_folds_n,
            "forward_only_wf_median_fold_expectancy_r": wf_ac.get("median_fold_expectancy_r"),
            "forward_only_wf_positive_fold_ratio": wf_ac.get("positive_fold_ratio"),
            "forward_only_wf_stitched_closed_trades": (wf_ac.get("stitched_summary") or {}).get("closed", 0),
        }

    holm_pvals = _holm_bonferroni_adjust(raw_pvals)
    primary_evals_14: Dict[str, Any] = {}
    passing_dev_arms: List[str] = []
    for ac in KH03_PRIMARY_ARMS:
        m = raw_arm_metrics[ac]
        m["holm_bonferroni_adjusted_pvalue"] = holm_pvals[ac]
        mr = matching_reports[ac]
        is_h3 = (ac == KH03_R2_H3_ADVISORY_ARM)
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
            executable_edge_eligible=(not is_h3),
            disqualification_reason=(
                "H3_DISQUALIFIED_PATH_B_FILTER_ONLY_NOT_EXECUTABLE_WITH_M1" if is_h3 else None
            ),
        )
        m["ci_parsed"] = ev["ci_parsed"]
        m["ci_schema_valid"] = ev["ci_schema_valid"]
        m["subgates"] = ev["subgates"]
        m["failed_advancement_criteria"] = ev["failed_advancement_criteria"]
        m["passes_preregistered_dev_advancement"] = ev["passes_preregistered_dev_advancement"]
        m["arm_dev_verdict"] = ev["arm_dev_verdict"]
        m["executable_edge_eligible"] = (not is_h3)
        primary_evals_14[ac] = m
        if ev["passes_preregistered_dev_advancement"]:
            passing_dev_arms.append(ac)

    power_grid = compute_prospective_oos_power_requirements_r2_c1(num_primary_arms=3)
    all_ci_valid = all(bool(v["ci_schema_valid"]) for v in primary_evals_14.values())

    f14: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH03_R2,
        "schema_version": SCHEMA_VERSION_KH03_R2,
        "preregistered_hypothesis_budget_m": 3,
        "multiplicity_correction": "HOLM_BONFERRONI_FAMILY_M3_NOT_RELAXED",
        "matching_disclosure": (
            "`causal_comparison_eligible_only_not_proof=True` certifies covariate balance on common support "
            "for descriptive comparison only — NEVER proof that filtering causally creates alpha on reused DEV."
        ),
        "primary_arm_evaluations": primary_evals_14,
        "matching_reports": matching_reports,
        "negative_control_summaries_1x": {nc: base_replays_1x[nc] for nc in KH03_NEGATIVE_CONTROLS},
        "kh04_prospective_oos_power_grid": power_grid,
        "all_ci_schemas_valid": all_ci_valid,
        "status": "PASS" if all_ci_valid else "FAIL",
    }
    f14["artifact_canonical_sha256"] = canonical_hash({k: v for k, v in f14.items() if k != "artifact_canonical_sha256"})

    # 16: Realized tail loss & 1-position portfolio disclosure
    arm_tail: Dict[str, Any] = {}
    global_max_loss = 0.0
    for ac in KH03_ALL_ARMS:
        eps = base_episodes_1x[ac]
        losses = sorted(abs(float(e["realized_net_pnl_usd"])) for e in eps if e["filled"] and e["outcome_label"] == "LOSS")
        over_250 = sum(1 for x in losses if x > PLANNED_NET_RISK_CAP_USD + 1e-9)
        mx = round(max(losses), 4) if losses else 0.0
        if mx > global_max_loss:
            global_max_loss = mx
        arm_tail[ac] = {
            "scheduled": len(eps),
            "closed": sum(1 for e in eps if e["filled"] and e["outcome_label"] in ("WIN", "LOSS")),
            "wins": sum(1 for e in eps if e["filled"] and e["outcome_label"] == "WIN"),
            "losses": len(losses),
            "open_at_horizon": sum(1 for e in eps if e["filled"] and e["outcome_label"] == "OPEN_AT_HORIZON"),
            "closed_total_fees_usd": base_replays_1x[ac].get("total_fees_usd"),
            "accrued_open_entry_fees_usd": base_replays_1x[ac].get("accrued_open_entry_fees_usd"),
            "realized_losses_exceeding_2p50_count": over_250,
            "max_realized_net_loss_usd": mx,
            "realized_loss_quantiles_usd": {
                "p50": _nearest_rank_quantile(losses, 0.50),
                "p90": _nearest_rank_quantile(losses, 0.90),
                "p95": _nearest_rank_quantile(losses, 0.95),
                "max": mx,
            },
        }

    f16: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH03_R2,
        "schema_version": SCHEMA_VERSION_KH03_R2,
        "risk_cap_usd": PLANNED_NET_RISK_CAP_USD,
        "planned_cap_is_physical_guarantee": False,
        "global_max_realized_net_loss_1x_usd": global_max_loss,
        "total_overlap_violations_1x": total_ov_1x,
        "realized_tail_loss_disclosure": (
            "planned_net_risk_usd <= 2.50 USD is a causal pre-exit stop estimate, NOT a physical guarantee "
            "against adverse gaps or stop-fill slippage. Overlapping arm x scenario rows MUST NOT be summed "
            "as independent trades."
        ),
        "arm_tail_and_fee_disclosures_1x": arm_tail,
        "status": "PASS" if total_ov_1x == 0 else "FAIL",
    }
    f16["artifact_canonical_sha256"] = canonical_hash({k: v for k, v in f16.items() if k != "artifact_canonical_sha256"})

    # 23: Honest Economic Win/Loss, DEV Eligibility & KH-04 Gate
    dev_verdict = "DEV_CANDIDATE_UNPROVEN_OOS" if passing_dev_arms else "NO_ECONOMIC_ADVANCEMENT_CANDIDATE"
    kh04_verdict = "BLOCKED_NO_ELIGIBLE_PROSPECTIVE_DATA" if passing_dev_arms else "BLOCKED_NO_DEV_CANDIDATE"
    f23: Dict[str, Any] = {
        "spec_version": SPEC_VERSION_KH03_R2,
        "schema_version": SCHEMA_VERSION_KH03_R2,
        "tested_code_sha": tested_code_sha,
        "passing_dev_candidate_arms": passing_dev_arms,
        "h3_policy_tier": h3_policy_07["H3_ECONOMIC_CLAIM_TIER"],
        "H3_RESIZE_EXECUTION_STATUS": h3_policy_07["H3_RESIZE_EXECUTION_STATUS"],
        "H3_FILTER_ONLY_RESEARCH": h3_policy_07["H3_FILTER_ONLY_RESEARCH"],
        "KH03_R2_DEV_CANDIDATE_VERDICT": dev_verdict,
        "RESEARCH_ECONOMIC_VERDICT": "NO_PROVEN_EDGE",
        "FRESH_OOS_VERDICT": "BLOCKED_NO_UNTOUCHED_DATA",
        "KH04_ENTRY": kh04_verdict,
        "ACCOUNT_EFFECTIVE_FEE_VERDICT": "UNKNOWN_NOT_LOCALLY_VERIFIED",
        "LIVE_NETWORK_VALIDATION": "NOT_RUN_BY_DESIGN",
        "OOS_EVALUATIONS": 0,
        "ACTIVATION_READY": "NO",
        "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2),
        "arm_win_loss_truth_table_1x": {
            ac: {
                "executable_edge_eligible": (ac in KH03_R2_EXECUTABLE_PRIMARY_ARMS),
                "closed": base_replays_1x[ac].get("closed"),
                "wins": base_replays_1x[ac].get("wins"),
                "losses": base_replays_1x[ac].get("losses"),
                "net_pnl_usd": base_replays_1x[ac].get("net_pnl_usd"),
                "total_fees_usd": base_replays_1x[ac].get("total_fees_usd"),
                "expectancy_r": base_replays_1x[ac].get("expectancy_r"),
            }
            for ac in KH03_ALL_ARMS
        },
        "h3_disclosure_ref_sha256": h3_disclosure_09["artifact_canonical_sha256"],
        "status": "PASS",
    }
    f23["artifact_canonical_sha256"] = canonical_hash({k: v for k, v in f23.items() if k != "artifact_canonical_sha256"})
    return f14, f15, f16, f23
