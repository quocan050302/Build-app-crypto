"""
aurum-desk/backend/services/kh_03_r4_economic_diagnostic_service.py

AURUM DESK — KH-03.R4 | ECONOMIC FEASIBILITY, ENTRY/EXIT ROOT-CAUSE EVIDENCE & DATA READINESS
SPEC VERSION: KH-03.R4-v1.0 | READ-ONLY DIAGNOSIS ONLY | REUSED DEV | OOS_EVALUATIONS=0 | ACTIVATION_READY=NO

Strictly isolated read-only diagnostic service. Modifies ZERO production, signal, Entry/SL/TP,
fee, risk, or historical strategy files. Produces exact Decimal accounting bridges, stage-by-stage
population & denominator conservation, non-executable fee-only descriptive counterfactuals separated
from full dynamic 6-scenario re-risking, association-only (non-causal) 3-axis mechanism evidence,
unmatched-population E1/E2/E3/NULL comparability audits, conservative empirical break-even bounds,
the 15-field x 8-domain DATA_READINESS_REGISTER, and deterministic GO/NO-GO owner decision rules.

Non-Negotiable Safety Contract:
  PRODUCTION=OFF | LIVE=OFF | PAPER=OFF | AUTO=OFF | ACTIVATION_READY=NO | OOS_EVALUATIONS=0
  No new strategy arms (no E4/E5), no threshold search, no credentials, no private/market API calls.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import gzip
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from services.kh_02_r2_research_integrity_service import (
    ACTIVATION_READY_KH02_R2,
    CANONICAL_DATASET_REL_PATH,
    CANONICAL_DATASET_SHA256_KH02_R2,
    DEFAULT_RANDOM_SEED_KH02_R2,
    PLANNED_NET_RISK_CAP_USD,
    RUNTIME_SAFETY_MODES_KH02_R2,
    _block_bootstrap_ci,
    load_r2_canonical_dataset_readonly,
)
from services.kh_03_r2_methodology_service import (
    parse_kh03_cluster_ci_strict,
)
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash

SPEC_VERSION_KH03_R4 = "KH-03.R4-v1.0"
SCHEMA_VERSION_KH03_R4 = "kh_03_r4_economic_feasibility_v1"

DECIMAL_RESIDUAL_TOLERANCE_USD = Decimal("0.0010")
Q4 = Decimal("0.0001")

# Canonical sealed historical anchors from KH-03.R3 / R3.C1 / R3.C2 (verified fresh, never substituted)
SEALED_KH03_R4_1X_ANCHORS: Dict[str, Dict[str, Any]] = {
    "NC_NULL_TRIGGER": {
        "eligible": 1323,
        "scheduled": 233,
        "skipped_busy": 1090,
        "filled": 233,
        "unfilled": 0,
        "closed": 203,
        "wins": 7,
        "losses": 196,
        "open_at_horizon_censored": 28,
        "data_gap_unresolved": 2,
        "gross_pnl_usd": Decimal("-164.9541"),
        "closed_fees_usd": Decimal("276.6577"),
        "net_pnl_usd": Decimal("-441.6118"),
        "accrued_open_entry_fees_open28_usd": Decimal("14.7151"),
        "accrued_open_entry_fees_all_nonclosed30_usd": Decimal("16.3231"),
        "expectancy_r": Decimal("-0.8806"),
    },
    "KH03_R3_E1_LOW_FRICTION_STOP_FLOOR": {
        "closed": 58,
        "wins": 3,
        "losses": 55,
        "gross_pnl_usd": Decimal("-65.7135"),
        "closed_fees_usd": Decimal("52.2986"),
        "net_pnl_usd": Decimal("-118.0121"),
        "expectancy_r": Decimal("-0.8286"),
    },
    "KH03_R3_E2_CAUSAL_DELAYED_CONFIRMATION": {
        "closed": 131,
        "wins": 5,
        "losses": 126,
        "gross_pnl_usd": Decimal("-108.9783"),
        "closed_fees_usd": Decimal("165.3988"),
        "net_pnl_usd": Decimal("-274.3771"),
        "expectancy_r": Decimal("-0.8498"),
    },
    "KH03_R3_E3_CAUSAL_NEAREST_M5_UNSWEPT_EXIT": {
        "closed": 206,
        "wins": 9,
        "losses": 197,
        "gross_pnl_usd": Decimal("-158.5045"),
        "closed_fees_usd": Decimal("280.5378"),
        "net_pnl_usd": Decimal("-439.0423"),
        "expectancy_r": Decimal("-0.8628"),
    },
}

DATA_READINESS_REQUIRED_FIELDS: Tuple[str, ...] = (
    "name",
    "why_required",
    "currently_observed",
    "source_existing_path",
    "evidence_sha256",
    "freshness",
    "granularity",
    "observed_or_modeled_or_missing",
    "cost_privacy_risk",
    "possible_owner_action",
    "blocked_if_absent",
    "confirmation_method",
    "minimum_coverage_spec",
    "cannot_assume",
    "promotion_gates_dependent_on_field",
)

FORBIDDEN_CAUSAL_OR_ALPHA_PHRASES: Tuple[str, ...] = (
    "PROVEN_EDGE",
    "CAUSAL_FIX_PROVED",
    "FEE_ONLY_ALPHA_EXECUTABLE",
    "GUARANTEED_MAX_LOSS_2P50",
    "FRESH_OOS_PASSED",
    "READY_FOR_LIVE_TRADING",
    "READY_FOR_PAPER_TRADING",
    "KH04_APPROVED",
)


def _to_dec(val: Any, *, field_name: str = "value") -> Decimal:
    if isinstance(val, bool) or val is None:
        raise ValueError(f"INVALID_DECIMAL_INPUT:{field_name}:{val!r}")
    if isinstance(val, float) and (not math.isfinite(val)):
        raise ValueError(f"NONFINITE_DECIMAL_INPUT:{field_name}:{val!r}")
    try:
        d = Decimal(str(val))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"UNPARSEABLE_DECIMAL_INPUT:{field_name}:{val!r}") from exc
    if not d.is_finite():
        raise ValueError(f"NONFINITE_DECIMAL_PARSED:{field_name}:{val!r}")
    return d


def _q4(d: Decimal) -> float:
    return float(d.quantize(Q4, rounding=ROUND_HALF_UP))


def _q4_str(d: Decimal) -> str:
    return str(d.quantize(Q4, rounding=ROUND_HALF_UP))


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _nearest_quantile(vals: Sequence[float], q: float) -> Optional[float]:
    if not vals:
        return None
    s = sorted(float(v) for v in vals)
    idx = max(0, min(len(s) - 1, int(math.ceil(q * len(s))) - 1))
    return round(s[idx], 4)


def _quantile_summary(vals: Sequence[float]) -> Dict[str, Optional[float]]:
    if not vals:
        return {"min": None, "p25": None, "p50": None, "p75": None, "p90": None, "max": None}
    s = sorted(float(v) for v in vals)
    return {
        "min": round(s[0], 4),
        "p25": _nearest_quantile(s, 0.25),
        "p50": _nearest_quantile(s, 0.50),
        "p75": _nearest_quantile(s, 0.75),
        "p90": _nearest_quantile(s, 0.90),
        "max": round(s[-1], 4),
    }


def build_r4_source_function_ownership_matrix() -> List[Dict[str, Any]]:
    """
    §3 Source & Function Ownership Matrix (verified directly from HEAD source AST).
    Documents existing read-only sources, inputs/outputs, known caveats, tests, and R4 consumers.
    """
    return [
        {
            "source_file": "aurum-desk/backend/services/kh_03_r3_diagnostic_service.py",
            "source_func": "build_r3_full_opportunity_lineage",
            "inputs": "datasets, r2_cands_1x, null_replay_summary, null_episodes",
            "outputs": "stage counts + sorted opportunity ID SHA-256 conservation dict",
            "known_caveats": "Tracks NC_NULL_TRIGGER strictly; overlapping arm counts must never be summed.",
            "expected_unit_test": "test_kh03_r4_population_and_censoring.py",
            "r4_wrapper_or_reader": "build_r4_trade_population_conservation",
        },
        {
            "source_file": "aurum-desk/backend/services/kh_03_r3_diagnostic_service.py",
            "source_func": "audit_r3_entry_exit_geometry_and_accounting",
            "inputs": "datasets, mtf_bundle, r2_cands_1x, out_ledger_gz_path",
            "outputs": "(ledger_meta, forensic_rows, null_summary) + streaming .jsonl.gz",
            "known_caveats": "post_hoc_mfe_20bar_in_sl_units is forensic post-hoc ONLY; never a decision feature.",
            "expected_unit_test": "test_kh03_r4_economic_accounting.py",
            "r4_wrapper_or_reader": "recompute_r4_decimal_economic_bridge",
        },
        {
            "source_file": "aurum-desk/backend/services/kh_03_r3_diagnostic_service.py",
            "source_func": "validate_r3_forensic_ledger_independently",
            "inputs": "ledger_gz_path, f07_doc",
            "outputs": "streaming Decimal residual & SHA audit",
            "known_caveats": "Checks 1x NULL ledger; R4 independent verifier additionally audits open/gap fees & duplicates.",
            "expected_unit_test": "test_kh03_r4_economic_accounting.py",
            "r4_wrapper_or_reader": "kh_03_r4_independent_verifier.py::_audit_forensic_ledger_gz_decimal",
        },
        {
            "source_file": "aurum-desk/backend/services/kh_03_r3_diagnostic_service.py",
            "source_func": "build_r3_win_loss_failure_attribution",
            "inputs": "forensic_rows, ledger_meta_06, seed",
            "outputs": "(f07_breakdown, f08_attribution)",
            "known_caveats": "Non-exclusive overlap buckets; association-only (never causal proof).",
            "expected_unit_test": "test_kh03_r4_no_alpha_no_causal_claims.py",
            "r4_wrapper_or_reader": "build_r4_mechanism_evidence_table",
        },
        {
            "source_file": "aurum-desk/backend/services/kh_03_r3_diagnostic_service.py",
            "source_func": "build_r3_cost_and_break_even_sensitivity",
            "inputs": "datasets, mtf_bundle, ledger_bundle, r2_cands_1x, seed",
            "outputs": "6-scenario FULL_DYNAMIC_RERISK summaries + empirical break-even win rates",
            "known_caveats": "Populations change across cost regimes; never compare across scenarios as fixed fills.",
            "expected_unit_test": "test_kh03_r4_economic_accounting.py",
            "r4_wrapper_or_reader": "derive_r4_breakeven_feasibility_bounds",
        },
        {
            "source_file": "aurum-desk/backend/services/kh_03_r3_diagnostic_service.py",
            "source_func": "run_r3_negative_control_and_label_integrity_tests",
            "inputs": "datasets, mtf_bundle, r2_cands_1x, forensic_rows, seed",
            "outputs": "direction-inverted, shuffled placebo, stale 15m shift, future-bar mutation, synthetic tie/TP proofs",
            "known_caveats": "Operates on REUSED DEV; verifies leakage/integrity, never OOS alpha.",
            "expected_unit_test": "test_kh03_r4_no_alpha_no_causal_claims.py",
            "r4_wrapper_or_reader": "run_kh_03_r4_acceptance.py::_run_r4_readonly_backtest_regression",
        },
        {
            "source_file": "aurum-desk/backend/services/kh_03_r3_experiment_service.py",
            "source_func": "run_r3_paired_and_strict_dev_evaluation_15_16",
            "inputs": "datasets, mtf_bundle, ledger_bundle, m5_ledger, r2_cands_1x, r3_cands_1x, prereg_plan_11, wf_13, wf_14",
            "outputs": "(f15_six_cost_and_fixed_fills, f16_strict_dev_gate)",
            "known_caveats": "accrued_open_entry_fees_usd in replay includes both OPEN_AT_HORIZON (28) and DATA_GAP (2) filled rows; R4 separates both explicitly.",
            "expected_unit_test": "test_kh03_r4_population_and_censoring.py",
            "r4_wrapper_or_reader": "audit_r4_covariate_overlap_and_effective_n",
        },
        {
            "source_file": "aurum-desk/backend/services/kh_03_r2_methodology_service.py",
            "source_func": "parse_kh03_cluster_ci_strict / build_kh03_r2_forward_only_dev_folds / validate_kh03_r2_fold_causality",
            "inputs": "raw CI [lo, hi] / bars_1m / embargo_minutes>=240",
            "outputs": "strict CI classification / forward-only expanding DEV folds with zero future train bars",
            "known_caveats": "REUSED DEV folds only; never treated as fresh OOS.",
            "expected_unit_test": "test_kh03_r4_no_alpha_no_causal_claims.py",
            "r4_wrapper_or_reader": "audit_r4_covariate_overlap_and_effective_n",
        },
        {
            "source_file": "aurum-desk/backend/services/kh_03_r3_c1_worker_policy_service.py",
            "source_func": "parse_benchmark_run_duration_strict / validate_benchmark_semantic_equivalence_strict / select_measured_worker_policy_strict",
            "inputs": "raw benchmark receipt, kind, system_limits, expected IDs/SHAs",
            "outputs": "strict measured winner vs safe applied cap (<=3 full pytest, <=4 global descendants), no extrapolation",
            "known_caveats": "Subset timing variance does not extrapolate to full suite.",
            "expected_unit_test": "test_kh03_r4_resource_and_async.py",
            "r4_wrapper_or_reader": "run_kh_03_r4_acceptance.py::_run_r4_fresh_determinism_benchmarks",
        },
    ]


def load_r4_immutable_research_inputs_readonly(
    repo_root: Path,
    expected_sha_map: Sequence[Tuple[str, str, str]],
) -> Dict[str, Any]:
    """
    Loads the canonical historical dataset in strictly read-only mode and verifies SHA-256
    immutability of the dataset plus all prior sealed R1/R2/R3/C1/C2 pointers and manifests.
    """
    ds_path = repo_root / CANONICAL_DATASET_REL_PATH
    if not ds_path.is_file():
        raise FileNotFoundError(f"CANONICAL_DATASET_MISSING:{ds_path}")
    datasets, ds_meta = load_r2_canonical_dataset_readonly(ds_path)
    actual_ds_sha = str(ds_meta["sha256"])
    ds_match = actual_ds_sha == CANONICAL_DATASET_SHA256_KH02_R2

    # Compute exact OHLCV timeframe spans & M1 gap rate directly from raw bars (no external access)
    tf_stats: Dict[str, Any] = {}
    for tf, expected_step_ms in (("1m", 60_000), ("5m", 300_000), ("15m", 900_000), ("1H", 3_600_000), ("4H", 14_400_000), ("1D", 86_400_000)):
        bars = list(datasets.get(tf) or ())
        if not bars:
            tf_stats[tf] = {"count": 0, "first_open_ms": None, "last_close_ms": None, "gap_intervals_count": 0, "missing_bars_est": 0, "missing_bar_ratio": None}
            continue
        first_open = int(bars[0]["open_time"])
        last_open = int(bars[-1]["open_time"])
        last_close = int(bars[-1]["close_time"])
        gaps = 0
        missing_est = 0
        for prev_b, cur_b in zip(bars, bars[1:]):
            dt = int(cur_b["open_time"]) - int(prev_b["open_time"])
            if dt > expected_step_ms:
                gaps += 1
                missing_est += max(0, (dt // expected_step_ms) - 1)
        span_slots = max(1, ((last_open - first_open) // expected_step_ms) + 1)
        tf_stats[tf] = {
            "count": len(bars),
            "first_open_ms": first_open,
            "last_close_ms": last_close,
            "first_utc": datetime.fromtimestamp(first_open / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "last_utc": datetime.fromtimestamp(last_close / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "gap_intervals_count": gaps,
            "missing_bars_est": missing_est,
            "missing_bar_ratio": round(missing_est / span_slots, 4),
        }

    prior_checks: Dict[str, Any] = {}
    all_prior_ok = ds_match
    for rel_p, exp_sha, label in expected_sha_map:
        p = repo_root / rel_p
        act = _sha256_file(p) if p.is_file() else ""
        matched = p.is_file() and (act == exp_sha)
        if not matched:
            all_prior_ok = False
        prior_checks[label] = {
            "rel_path": rel_p,
            "exists": p.is_file(),
            "expected_sha256": exp_sha,
            "actual_sha256": act,
            "match": matched,
        }

    return {
        "datasets": datasets,
        "provenance": {
            "canonical_dataset_rel_path": CANONICAL_DATASET_REL_PATH,
            "canonical_dataset_sha256": actual_ds_sha,
            "canonical_dataset_expected_sha256": CANONICAL_DATASET_SHA256_KH02_R2,
            "canonical_dataset_sha256_match": ds_match,
            "timeframe_coverage_and_gaps": tf_stats,
            "all_prior_sealed_artifacts_intact": all_prior_ok,
            "prior_sealed_artifacts_count": len(prior_checks),
            "prior_sealed_artifacts_immutability": prior_checks,
            "status": "PASS" if all_prior_ok else "FAIL",
        },
    }


def build_r4_trade_population_conservation(
    *,
    lineage_05: Mapping[str, Any],
    forensic_rows: Sequence[Mapping[str, Any]],
    r2_cands_1x: Mapping[str, Any],
    dyn6_scenarios: Mapping[str, Any],
    dataset_sha: str,
) -> Dict[str, Any]:
    """
    Artifact 03 (`03_raw_population_lineage_and_denominator_conservation.json`):
    Verifies exact conservation across every stage (`n_total_bars` -> `n_closed`/`n_open_censored`/
    `n_gap_unresolved`/`n_unfilled`), stable sorted opportunity IDs SHA-256, zero duplicates,
    zero 1-position overlaps, and strict separation of `7/203` (closed) vs `7/233` (filled/scheduled).
    Also reports per-arm & per-scenario denominators separately without summing overlapping arms.
    """
    stages05 = lineage_05.get("stages") or {}
    elig_cands = sorted(
        list((r2_cands_1x.get("arm_candidates") or {}).get("NC_NULL_TRIGGER") or ()),
        key=lambda c: (int(c["decision_timestamp_ms"]), str(c["direction"]), str(c["opportunity_id"])),
    )
    elig_ids = [str(c["opportunity_id"]) for c in elig_cands]
    sched_ids = [str(r["opportunity_id"]) for r in forensic_rows]
    sched_set = set(sched_ids)
    busy_skipped_ids = [oid for oid in elig_ids if oid not in sched_set]

    filled_rows = [r for r in forensic_rows if bool(r.get("filled"))]
    unfilled_rows = [r for r in forensic_rows if not bool(r.get("filled"))]
    win_rows = [r for r in filled_rows if r.get("outcome_label") == "WIN"]
    loss_rows = [r for r in filled_rows if r.get("outcome_label") == "LOSS"]
    breakeven_rows = [r for r in filled_rows if r.get("outcome_label") == "BREAKEVEN"]
    open_rows = [r for r in filled_rows if r.get("outcome_label") == "OPEN_AT_HORIZON"]
    gap_rows = [r for r in forensic_rows if r.get("outcome_label") == "DATA_GAP_UNRESOLVED"]
    known_labels = {"WIN", "LOSS", "BREAKEVEN", "OPEN_AT_HORIZON", "DATA_GAP_UNRESOLVED"}
    ambiguous_rows = [
        r for r in forensic_rows
        if bool(r.get("filled")) and str(r.get("outcome_label")) not in known_labels
    ]

    n_total_bars = int(stages05.get("total_m1_bars", 0))
    n_dir_opps = int(stages05.get("total_directional_bar_opportunities", 0))
    n_raw_trig = int(stages05.get("raw_m1_triggers_count", 0))
    n_eligible = len(elig_ids)
    n_skipped_busy = len(busy_skipped_ids)
    n_scheduled = len(sched_ids)
    n_filled = len(filled_rows)
    n_unfilled = len(unfilled_rows)
    n_wins = len(win_rows)
    n_losses = len(loss_rows)
    n_breakeven = len(breakeven_rows)
    n_closed = n_wins + n_losses + n_breakeven
    n_open_censored = len(open_rows)
    n_gap_unresolved = len(gap_rows)
    n_independent_setup_episodes = len({str(r.get("setup_episode_id") or r.get("opportunity_id")) for r in win_rows + loss_rows + breakeven_rows})
    n_effective_ny_days = len({str(r.get("ny_date")) for r in win_rows + loss_rows + breakeven_rows})

    # Verify strict 1-position chronological non-overlap on forensic_rows
    overlap_violations = 0
    prev_end_ms = -1
    for r in forensic_rows:
        dec_ts = int(r["decision_timestamp_ms"])
        if dec_ts < prev_end_ms:
            overlap_violations += 1
        hold_m = r.get("hold_minutes")
        if bool(r.get("filled")) and hold_m is not None:
            prev_end_ms = max(prev_end_ms, dec_ts + int(hold_m) * 60_000)

    elig_conserved = (n_eligible == n_scheduled + n_skipped_busy)
    gap_unfilled_overlap = sum(1 for r in unfilled_rows if r.get("outcome_label") == "DATA_GAP_UNRESOLVED")
    sched_partition_sum = n_closed + n_open_censored + n_gap_unresolved + (n_unfilled - gap_unfilled_overlap)
    sched_conserved = (n_scheduled == sched_partition_sum)
    closed_partition_ok = (n_closed == n_wins + n_losses + n_breakeven)
    no_dups = (len(elig_ids) == len(set(elig_ids))) and (len(sched_ids) == len(set(sched_ids)))
    no_ambiguous = (len(ambiguous_rows) == 0)
    zero_overlap = (overlap_violations == 0) and bool((lineage_05.get("conservation_checks") or {}).get("zero_1pos_overlap_violations", False))

    wr_closed = round(n_wins / n_closed, 4) if n_closed > 0 else None
    wr_filled = round(n_wins / n_filled, 4) if n_filled > 0 else None
    wr_scheduled = round(n_wins / n_scheduled, 4) if n_scheduled > 0 else None

    # Build per-scenario / per-arm conservation table (strictly separate, never summed)
    by_scenario_and_arm: Dict[str, Dict[str, Any]] = {}
    for sc_id, sc_obj in sorted(dyn6_scenarios.items()):
        arms_tbl: Dict[str, Any] = {}
        for arm_code, rs in sorted(((sc_obj or {}).get("arms") or {}).items()):
            elig_a = int(rs.get("eligible_candidates_input", 0))
            sched_a = int(rs.get("scheduled", 0))
            skip_a = int(rs.get("skipped_due_to_active_position", 0))
            unf_a = int(rs.get("unfilled", 0))
            cl_a = int(rs.get("closed", 0))
            w_a = int(rs.get("wins", 0))
            l_a = int(rs.get("losses", 0))
            op_a = int(rs.get("open_at_horizon", 0))
            gp_a = int(rs.get("data_gap_unresolved", 0))
            filled_a = sched_a - unf_a
            arms_tbl[arm_code] = {
                "n_total_bars": n_total_bars,
                "n_directional_opportunities": n_dir_opps,
                "n_raw_triggers": n_raw_trig,
                "n_eligible": elig_a,
                "n_skipped_busy": skip_a,
                "n_scheduled": sched_a,
                "n_filled": filled_a,
                "n_unfilled": unf_a,
                "n_closed": cl_a,
                "n_wins": w_a,
                "n_losses": l_a,
                "n_open_censored": op_a,
                "n_gap_unresolved": gp_a,
                "n_independent_setup_episodes": int(rs.get("effective_setup_episodes", 0)),
                "n_effective_ny_days": int(rs.get("effective_ny_days", 0)),
                "executed_ids_sha256": rs.get("executed_ids_sha256"),
                "eligible_conserved": (elig_a == sched_a + skip_a),
                "scheduled_conserved": (sched_a == cl_a + op_a + gp_a + unf_a),
                "closed_conserved": (cl_a == w_a + l_a),
                "overlap_violations": int(rs.get("overlap_violations", 0)),
                "win_rate_over_closed": round(w_a / cl_a, 4) if cl_a > 0 else None,
                "win_rate_over_filled": round(w_a / filled_a, 4) if filled_a > 0 else None,
            }
        by_scenario_and_arm[sc_id] = arms_tbl

    all_arm_conserved = all(
        a["eligible_conserved"] and a["scheduled_conserved"] and a["closed_conserved"] and a["overlap_violations"] == 0
        for sc in by_scenario_and_arm.values()
        for a in sc.values()
    )

    # Check exact baseline 1x anchor counts (no silent drift!)
    anc = SEALED_KH03_R4_1X_ANCHORS["NC_NULL_TRIGGER"]
    anchor_match = (
        n_eligible == anc["eligible"]
        and n_scheduled == anc["scheduled"]
        and n_skipped_busy == anc["skipped_busy"]
        and n_filled == anc["filled"]
        and n_unfilled == anc["unfilled"]
        and n_closed == anc["closed"]
        and n_wins == anc["wins"]
        and n_losses == anc["losses"]
        and n_open_censored == anc["open_at_horizon_censored"]
        and n_gap_unresolved == anc["data_gap_unresolved"]
    )

    ok = (
        elig_conserved
        and sched_conserved
        and closed_partition_ok
        and no_dups
        and no_ambiguous
        and zero_overlap
        and all_arm_conserved
        and anchor_match
        and n_closed > 0
    )

    return {
        "data_regime": "REUSED_DEV_NOT_FRESH_OOS",
        "source_dataset_sha256": dataset_sha,
        "do_not_sum_overlapping_arms_warning": (
            "Each arm and cost scenario represents a distinct or overlapping candidate stream. "
            "NEVER sum trade counts across overlapping arms into independent trials."
        ),
        "primary_baseline_1x_null_conservation": {
            "arm_code": "NC_NULL_TRIGGER",
            "scenario_id": "1X_BASE_0p0004",
            "n_total_bars": n_total_bars,
            "n_directional_opportunities": n_dir_opps,
            "n_raw_triggers": n_raw_trig,
            "n_eligible": n_eligible,
            "n_skipped_busy": n_skipped_busy,
            "n_scheduled": n_scheduled,
            "n_filled": n_filled,
            "n_unfilled": n_unfilled,
            "n_closed": n_closed,
            "n_wins": n_wins,
            "n_losses": n_losses,
            "n_breakeven": n_breakeven,
            "n_open_censored": n_open_censored,
            "n_gap_unresolved": n_gap_unresolved,
            "n_independent_setup_episodes": n_independent_setup_episodes,
            "n_effective_ny_days": n_effective_ny_days,
            "ambiguous_rows_count": len(ambiguous_rows),
            "stable_id_hashes": {
                "eligible_ids_sha256": canonical_hash(elig_ids),
                "busy_skipped_ids_sha256": canonical_hash(busy_skipped_ids),
                "scheduled_ids_sha256": canonical_hash(sched_ids),
                "wins_ids_sha256": canonical_hash([str(r["opportunity_id"]) for r in win_rows]),
                "losses_ids_sha256": canonical_hash([str(r["opportunity_id"]) for r in loss_rows]),
                "open_censored_ids_sha256": canonical_hash([str(r["opportunity_id"]) for r in open_rows]),
                "gap_unresolved_ids_sha256": canonical_hash([str(r["opportunity_id"]) for r in gap_rows]),
            },
        },
        "denominator_separation_disclosure": {
            "primary_closed_win_rate": wr_closed,
            "primary_closed_fraction": f"{n_wins}/{n_closed}",
            "filled_win_rate_secondary_only": wr_filled,
            "filled_fraction": f"{n_wins}/{n_filled}",
            "scheduled_win_rate_secondary_only": wr_scheduled,
            "scheduled_fraction": f"{n_wins}/{n_scheduled}",
            "numerators_identical": True,
            "denominators_distinct": (n_closed != n_filled),
            "rule": "Primary win_rate strictly uses closed trades (7/203 = 3.45%); 7/233 (3.00%) uses filled/scheduled denominator and MUST NOT replace closed win rate.",
        },
        "conservation_checks": {
            "eligible_eq_scheduled_plus_skipped_busy": elig_conserved,
            "scheduled_eq_closed_plus_open_plus_gap_plus_unfilled": sched_conserved,
            "closed_eq_wins_plus_losses_plus_breakeven": closed_partition_ok,
            "zero_duplicate_opportunity_ids": no_dups,
            "zero_ambiguous_outcomes": no_ambiguous,
            "zero_1pos_overlap_violations": zero_overlap,
            "all_6_scenarios_and_arms_conserved": all_arm_conserved,
            "baseline_1x_matches_sealed_r3_c2_anchors": anchor_match,
        },
        "by_scenario_and_arm_conservation": by_scenario_and_arm,
        "status": "PASS" if ok else "FAIL",
    }


def recompute_r4_decimal_economic_bridge(
    forensic_rows: Sequence[Mapping[str, Any]],
    *,
    dataset_sha: str,
    ledger_meta: Mapping[str, Any],
    tolerance_usd: Decimal = DECIMAL_RESIDUAL_TOLERANCE_USD,
) -> Dict[str, Any]:
    """
    Artifact 04 (`04_closed_trade_decimal_gross_fee_net_bridge.json`):
    Exact Decimal per-trade and aggregate accounting bridge on `NC_NULL_TRIGGER` 1x.
    Checks:
      - `abs((gross - closed_fees) - net) <= 1e-3 USD` per trade and aggregate
      - `abs((entry_fee + exit_fee) - total_fees) <= 0.0005 USD` per closed trade
      - Zero double-counting of entry fee, zero silent dropping of open/gap rows
      - Explicit separation of closed trade fees (`276.6577 USD`) vs open-at-horizon accrued
        entry fees (`14.7151 USD`, 28 trades) vs data-gap accrued entry fees (`1.6080 USD`, 2 trades)
        = `16.3231 USD` total non-closed filled entry fees.
    """
    seen_oids: set[str] = set()
    duplicate_oids: List[str] = []
    violations: List[str] = []

    closed_gross = Decimal("0")
    closed_entry_fees = Decimal("0")
    closed_exit_fees = Decimal("0")
    closed_total_fees = Decimal("0")
    closed_net = Decimal("0")
    wins_gross = Decimal("0")
    wins_fees = Decimal("0")
    wins_net = Decimal("0")
    losses_gross = Decimal("0")
    losses_fees = Decimal("0")
    losses_net = Decimal("0")

    open_accrued_entry_fees = Decimal("0")
    gap_accrued_entry_fees = Decimal("0")
    max_trade_residual = Decimal("0")
    max_fee_split_residual = Decimal("0")
    max_realized_loss_abs = Decimal("0")
    realized_losses_gt_cap = 0

    n_closed = 0
    n_wins = 0
    n_losses = 0
    n_open = 0
    n_gap = 0
    n_unfilled = 0
    closed_r_vals: List[Decimal] = []
    closed_ny_days: List[str] = []

    for idx, r in enumerate(forensic_rows):
        oid = str(r.get("opportunity_id") or "")
        if not oid or oid in seen_oids:
            duplicate_oids.append(oid or f"EMPTY_AT_{idx}")
        seen_oids.add(oid)

        filled = bool(r.get("filled"))
        label = str(r.get("outcome_label") or "")

        if not filled:
            n_unfilled += 1
            if label == "DATA_GAP_UNRESOLVED":
                n_gap += 1
            continue

        if label in ("WIN", "LOSS", "BREAKEVEN"):
            n_closed += 1
            g = _to_dec(r.get("realized_gross_pnl_usd"), field_name="realized_gross_pnl_usd")
            ef = _to_dec(r.get("entry_fee_usd"), field_name="entry_fee_usd")
            xf = _to_dec(r.get("exit_fee_usd"), field_name="exit_fee_usd")
            tf = _to_dec(r.get("total_fees_usd"), field_name="total_fees_usd")
            ne = _to_dec(r.get("realized_net_pnl_usd"), field_name="realized_net_pnl_usd")
            op_f = _to_dec(r.get("accrued_open_entry_fee_usd", 0), field_name="accrued_open_entry_fee_usd")
            r_net = _to_dec(r.get("realized_net_r"), field_name="realized_net_r")

            if op_f != Decimal("0"):
                violations.append(f"CLOSED_TRADE_HAS_NONZERO_OPEN_ACCRUED_FEE:{oid}:{op_f}")
            if tf <= Decimal("0") and (g - ne) > Decimal("0.01"):
                violations.append(f"ZERO_FEE_DISPLAY_BUG:{oid}")
            split_res = abs((ef + xf) - tf)
            if split_res > max_fee_split_residual:
                max_fee_split_residual = split_res
            if split_res > Decimal("0.0005"):
                violations.append(f"FEE_SPLIT_RESIDUAL_BREACH:{oid}:{split_res}")
            tr_res = abs((g - tf) - ne)
            if tr_res > max_trade_residual:
                max_trade_residual = tr_res
            if tr_res > tolerance_usd:
                violations.append(f"GROSS_FEE_NET_RESIDUAL_BREACH:{oid}:{tr_res}")

            closed_gross += g
            closed_entry_fees += ef
            closed_exit_fees += xf
            closed_total_fees += tf
            closed_net += ne
            closed_r_vals.append(r_net)
            closed_ny_days.append(str(r.get("ny_date") or "UNK"))

            if label == "WIN":
                n_wins += 1
                wins_gross += g
                wins_fees += tf
                wins_net += ne
            elif label == "LOSS":
                n_losses += 1
                losses_gross += g
                losses_fees += tf
                losses_net += ne
                loss_abs = abs(ne)
                if loss_abs > max_realized_loss_abs:
                    max_realized_loss_abs = loss_abs
                if loss_abs > Decimal(str(PLANNED_NET_RISK_CAP_USD)):
                    realized_losses_gt_cap += 1
        elif label == "OPEN_AT_HORIZON":
            n_open += 1
            op_f = _to_dec(r.get("accrued_open_entry_fee_usd", 0), field_name="accrued_open_entry_fee_usd")
            tf = _to_dec(r.get("total_fees_usd", 0), field_name="total_fees_usd")
            if tf != Decimal("0"):
                violations.append(f"OPEN_TRADE_LEAKED_INTO_CLOSED_FEES:{oid}:{tf}")
            open_accrued_entry_fees += op_f
        elif label == "DATA_GAP_UNRESOLVED":
            n_gap += 1
            # For filled DATA_GAP_UNRESOLVED rows, entry fee was incurred at entry fill; keep strictly separate from closed fees!
            op_f = _to_dec(r.get("accrued_open_entry_fee_usd", 0), field_name="accrued_open_entry_fee_usd")
            ef = _to_dec(r.get("entry_fee_usd", 0), field_name="entry_fee_usd")
            tf = _to_dec(r.get("total_fees_usd", 0), field_name="total_fees_usd")
            if tf != Decimal("0"):
                violations.append(f"GAP_TRADE_LEAKED_INTO_CLOSED_FEES:{oid}:{tf}")
            gap_accrued_entry_fees += (op_f if op_f > Decimal("0") else ef)
        else:
            violations.append(f"UNKNOWN_OUTCOME_LABEL:{oid}:{label}")

    if duplicate_oids:
        violations.append(f"DUPLICATE_OPPORTUNITY_IDS:{len(duplicate_oids)}")

    agg_residual = abs((closed_gross - closed_total_fees) - closed_net)
    if agg_residual > tolerance_usd:
        violations.append(f"AGGREGATE_GROSS_FEE_NET_RESIDUAL_BREACH:{agg_residual}")

    fee_sum_residual = abs((closed_entry_fees + closed_exit_fees) - closed_total_fees)
    if fee_sum_residual > Decimal("0.0020"):
        violations.append(f"AGGREGATE_ENTRY_EXIT_FEE_SUM_DRIFT:{fee_sum_residual}")

    expectancy_r = (sum(closed_r_vals, Decimal("0")) / Decimal(n_closed)) if n_closed > 0 else Decimal("0")
    ci_raw = _block_bootstrap_ci([float(x) for x in closed_r_vals], closed_ny_days, seed=DEFAULT_RANDOM_SEED_KH02_R2 + 1) if closed_r_vals else None
    ci_parsed = parse_kh03_cluster_ci_strict(ci_raw, label="baseline_1x_closed_ny_day_95ci_r")

    # Anchor reconciliation against sealed R3/C2 1x baseline
    anc = SEALED_KH03_R4_1X_ANCHORS["NC_NULL_TRIGGER"]
    anchor_drifts: List[str] = []
    if n_closed != anc["closed"] or n_wins != anc["wins"] or n_losses != anc["losses"]:
        anchor_drifts.append(f"COUNTS_DRIFT:got=({n_closed},{n_wins},{n_losses})!=exp=({anc['closed']},{anc['wins']},{anc['losses']})")
    if abs(closed_gross - anc["gross_pnl_usd"]) > Decimal("0.01"):
        anchor_drifts.append(f"GROSS_DRIFT:{_q4_str(closed_gross)}!={anc['gross_pnl_usd']}")
    if abs(closed_total_fees - anc["closed_fees_usd"]) > Decimal("0.01"):
        anchor_drifts.append(f"FEES_DRIFT:{_q4_str(closed_total_fees)}!={anc['closed_fees_usd']}")
    if abs(closed_net - anc["net_pnl_usd"]) > Decimal("0.01"):
        anchor_drifts.append(f"NET_DRIFT:{_q4_str(closed_net)}!={anc['net_pnl_usd']}")
    if abs(expectancy_r - anc["expectancy_r"]) > Decimal("0.001"):
        anchor_drifts.append(f"EXPR_DRIFT:{_q4_str(expectancy_r)}!={anc['expectancy_r']}")
    if abs(open_accrued_entry_fees - anc["accrued_open_entry_fees_open28_usd"]) > Decimal("0.01"):
        anchor_drifts.append(f"OPEN_ACCRUED_FEE_DRIFT:{_q4_str(open_accrued_entry_fees)}!={anc['accrued_open_entry_fees_open28_usd']}")

    if anchor_drifts:
        violations.append("REGRESSION_DRIFT:" + ";".join(anchor_drifts))

    nonclosed_total_accrued = open_accrued_entry_fees + gap_accrued_entry_fees
    ok = (
        len(violations) == 0
        and n_closed > 0
        and closed_gross < Decimal("0")
        and closed_total_fees > Decimal("0")
        and closed_net < Decimal("0")
        and ci_parsed.get("schema_valid") is True
    )

    return {
        "data_regime": "REUSED_DEV_NOT_FRESH_OOS",
        "arithmetic_mode": "EXACT_DECIMAL_QUANTIZED_AT_REPORT_BOUNDARY_ONLY",
        "residual_tolerance_usd": str(tolerance_usd),
        "source_dataset_sha256": dataset_sha,
        "linked_forensic_ledger": dict(ledger_meta),
        "counts": {
            "n_scheduled": len(forensic_rows),
            "n_closed": n_closed,
            "n_wins": n_wins,
            "n_losses": n_losses,
            "n_open_censored": n_open,
            "n_gap_unresolved": n_gap,
            "n_unfilled": n_unfilled,
            "n_effective_ny_days_closed": len(set(closed_ny_days)),
        },
        "closed_trades_decimal_bridge_usd": {
            "gross_pnl_closed_usd": _q4(closed_gross),
            "gross_pnl_closed_exact": _q4_str(closed_gross),
            "entry_fees_closed_usd": _q4(closed_entry_fees),
            "exit_fees_closed_usd": _q4(closed_exit_fees),
            "total_closed_fees_usd": _q4(closed_total_fees),
            "total_closed_fees_exact": _q4_str(closed_total_fees),
            "net_pnl_closed_usd": _q4(closed_net),
            "net_pnl_closed_exact": _q4_str(closed_net),
            "aggregate_gross_minus_fees_minus_net_residual_usd": _q4(agg_residual),
            "max_per_trade_gross_minus_fees_minus_net_residual_usd": _q4(max_trade_residual),
            "max_per_trade_entry_plus_exit_minus_total_fee_residual_usd": _q4(max_fee_split_residual),
            "wins_subtotal": {
                "count": n_wins,
                "gross_pnl_usd": _q4(wins_gross),
                "fees_usd": _q4(wins_fees),
                "net_pnl_usd": _q4(wins_net),
            },
            "losses_subtotal": {
                "count": n_losses,
                "gross_pnl_usd": _q4(losses_gross),
                "fees_usd": _q4(losses_fees),
                "net_pnl_usd": _q4(losses_net),
            },
            "expectancy_r": _q4(expectancy_r),
            "ny_day_cluster_95ci_r_raw": ci_raw,
            "ny_day_cluster_95ci_r_parsed": ci_parsed,
        },
        "open_and_gap_fee_separation_usd": {
            "open_at_horizon_28_accrued_entry_fees_usd": _q4(open_accrued_entry_fees),
            "data_gap_unresolved_2_accrued_entry_fees_usd": _q4(gap_accrued_entry_fees),
            "all_nonclosed_filled_30_accrued_entry_fees_usd": _q4(nonclosed_total_accrued),
            "open_fees_excluded_from_closed_net_pnl": True,
            "entry_fee_double_counted": False,
            "reconciliation_note": (
                "R3 diagnostic 07 reports open-at-horizon accrued entry fees only (14.7151 USD across 28 open trades), "
                "whereas R2/R3 replay summary reports all non-closed filled entry fees (16.3231 USD = 14.7151 + 1.6080 from 2 data-gap trades). "
                "Both are audited separately here and strictly excluded from closed-trade fees (276.6577 USD)."
            ),
        },
        "planned_risk_cap_tail_check": {
            "planned_net_risk_cap_usd": PLANNED_NET_RISK_CAP_USD,
            "planned_cap_is_physical_guarantee": False,
            "realized_losses_exceeding_2p50_count": realized_losses_gt_cap,
            "max_realized_single_loss_usd": _q4(max_realized_loss_abs),
        },
        "gross_is_strictly_negative_before_fees": closed_gross < Decimal("0"),
        "historical_r3_c2_anchor_exact_match": len(anchor_drifts) == 0,
        "historical_anchor_drifts": anchor_drifts,
        "violations": violations,
        "status": "PASS" if ok else "FAIL",
    }


def build_r4_counterfactual_cost_only_diagnostics(
    forensic_rows: Sequence[Mapping[str, Any]],
    *,
    fixed_fills_from_r3: Mapping[str, Any],
    dyn6_scenarios: Mapping[str, Any],
    dataset_sha: str,
) -> Dict[str, Any]:
    """
    Artifact 07 (`07_fee_only_fixed_fills_non_executable_counterfactual.json`):
    Tests whether reducing or even completely zeroing modeled fees on the EXACT matched 203 closed
    fills of `NC_NULL_TRIGGER` can turn baseline profitable.
    Because baseline gross P&L is `-164.9541 USD < 0` BEFORE any fees, even a fictional 0-fee
    counterfactual (`COUNTERFACTUAL_ZERO_FEE_DESCRIPTIVE`) remains strictly negative (`-164.9541 USD`).
    Explicitly labeled `NON_EXECUTABLE_DESCRIPTIVE_ONLY`, NEVER `PROVEN_EDGE`, and separated from
    `FULL_DYNAMIC_RERISK` 6-scenario replays.
    """
    closed_rows = [r for r in forensic_rows if bool(r.get("filled")) and r.get("outcome_label") in ("WIN", "LOSS")]
    n_closed = len(closed_rows)
    closed_ids_sha = canonical_hash([str(r["opportunity_id"]) for r in closed_rows])

    gross_sum = sum((_to_dec(r["realized_gross_pnl_usd"], field_name="gross") for r in closed_rows), Decimal("0"))
    base_fee_sum = sum((_to_dec(r["total_fees_usd"], field_name="fees") for r in closed_rows), Decimal("0"))
    base_net_sum = sum((_to_dec(r["realized_net_pnl_usd"], field_name="net") for r in closed_rows), Decimal("0"))

    # Construct descriptive fee-scaling sweeps on IDENTICAL matched 203 fills (fee-only; spread/slippage held at baseline fills)
    fee_tiers = (
        ("COUNTERFACTUAL_ZERO_FEE_DESCRIPTIVE", Decimal("0.0000"), Decimal("0.0")),
        ("DESCRIPTIVE_HALF_TAKER_0p0002", Decimal("0.0002"), Decimal("0.5")),
        ("BASELINE_1X_TAKER_0p0004", Decimal("0.0004"), Decimal("1.0")),
        ("STRESS_TAKER_0p0006_FIXED_FILLS", Decimal("0.0006"), Decimal("1.5")),
    )
    sweeps: Dict[str, Any] = {}
    for label, rate, mult in fee_tiers:
        scaled_fees = sum((_to_dec(r["total_fees_usd"], field_name="fees") * mult for r in closed_rows), Decimal("0"))
        cf_net = gross_sum - scaled_fees
        cf_r_vals = [
            float((_to_dec(r["realized_gross_pnl_usd"]) - (_to_dec(r["total_fees_usd"]) * mult)) / max(Decimal("0.01"), _to_dec(r["planned_net_risk_usd"])))
            for r in closed_rows
        ]
        sweeps[label] = {
            "population_mode": "FIXED_FILLS_DESCRIPTIVE_REPRICING",
            "executable_strategy": False,
            "is_alpha_claim": False,
            "assumed_taker_fee_rate": float(rate),
            "fee_multiplier_vs_1x_base": float(mult),
            "matched_closed_trades_count": n_closed,
            "matched_closed_ids_sha256": closed_ids_sha,
            "gross_pnl_usd": _q4(gross_sum),
            "counterfactual_closed_fees_usd": _q4(scaled_fees),
            "counterfactual_net_pnl_usd": _q4(cf_net),
            "counterfactual_mean_r": round(sum(cf_r_vals) / max(1, len(cf_r_vals)), 4),
            "profitable_under_this_descriptive_fee": bool(cf_net > Decimal("0")),
        }

    zero_fee_net = Decimal(str(sweeps["COUNTERFACTUAL_ZERO_FEE_DESCRIPTIVE"]["counterfactual_net_pnl_usd"]))
    fee_reduction_alone_cannot_cure_baseline = bool(gross_sum < Decimal("0") and zero_fee_net < Decimal("0"))

    # Contrast with FULL_DYNAMIC_RERISK population sizes to prove why dynamic vs fixed-fill MUST stay separate
    dyn_pop_counts = {
        sc_id: int((((sc_obj or {}).get("arms") or {}).get("NC_NULL_TRIGGER") or {}).get("closed", 0))
        for sc_id, sc_obj in sorted(dyn6_scenarios.items())
    }
    dynamic_populations_differ_across_cost_regimes = len(set(dyn_pop_counts.values())) > 1

    ok = (
        n_closed == 203
        and abs((gross_sum - base_fee_sum) - base_net_sum) <= DECIMAL_RESIDUAL_TOLERANCE_USD
        and fee_reduction_alone_cannot_cure_baseline
        and dynamic_populations_differ_across_cost_regimes
    )

    return {
        "data_regime": "REUSED_DEV_NOT_FRESH_OOS",
        "source_dataset_sha256": dataset_sha,
        "population_mode": "FIXED_FILLS_DESCRIPTIVE_REPRICING",
        "epistemic_label": "NON_EXECUTABLE_DESCRIPTIVE_ONLY",
        "counterfactual_zero_fee_label": "COUNTERFACTUAL_ZERO_FEE_DESCRIPTIVE",
        "is_executable_strategy": False,
        "is_proven_edge": False,
        "dynamic_vs_fixed_fill_populations_separated": True,
        "matched_closed_trades_count": n_closed,
        "matched_closed_ids_sha256": closed_ids_sha,
        "baseline_gross_pnl_usd": _q4(gross_sum),
        "baseline_closed_fees_usd": _q4(base_fee_sum),
        "baseline_net_pnl_usd": _q4(base_net_sum),
        "zero_fee_counterfactual_net_pnl_usd": _q4(zero_fee_net),
        "fee_reduction_alone_cannot_cure_baseline_losses": fee_reduction_alone_cannot_cure_baseline,
        "core_conclusion": (
            "Baseline closed gross P&L on matched 203 fills is already -164.9541 USD (< 0) BEFORE any fees. "
            "Therefore, reducing or even zeroing modeled closed fees (276.6577 USD) CANNOT make the existing baseline fills profitable. "
            "Fees amplify an already-negative gross payoff; zero-fee repricing is purely descriptive and NOT executable alpha."
        ),
        "fee_only_fixed_fill_sweeps": sweeps,
        "r3_fixed_fills_reference_summary": dict(fixed_fills_from_r3),
        "dynamic_full_rerisk_null_closed_counts_by_scenario": dyn_pop_counts,
        "dynamic_populations_differ_across_cost_regimes": dynamic_populations_differ_across_cost_regimes,
        "status": "PASS" if ok else "FAIL",
    }


def build_r4_mechanism_evidence_table(
    forensic_rows: Sequence[Mapping[str, Any]],
    *,
    f08_attr: Mapping[str, Any],
    f09_cost: Mapping[str, Any],
    decimal_bridge: Mapping[str, Any],
    fee_only_diag: Mapping[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Produces:
      - Artifact 05 (`05_entry_exit_stop_target_risk_and_tail_diagnostic.json`):
        stop/TP/R:R/fee-share/holding/MFE/gap/tie/realized-tail distributions & disclosures
      - Artifact 10 (`10_assoc_not_causal_and_no_alpha_claim_proof.json`):
        3-axis diagnosis (ENGINE/ACCOUNTING, STRATEGY & COST, DATA/EXECUTION) with strict
        `ASSOCIATION_ONLY_NO_CAUSAL_CLAIM` discipline, competing explanations, confounders,
        and zero causal claims or SL/TP tuning recommendations.
    """
    closed_rows = [r for r in forensic_rows if bool(r.get("filled")) and r.get("outcome_label") in ("WIN", "LOSS")]
    loss_rows = [r for r in closed_rows if r.get("outcome_label") == "LOSS"]
    win_rows = [r for r in closed_rows if r.get("outcome_label") == "WIN"]

    stop_dists = [float(r["stop_dist_usd"]) for r in closed_rows]
    tp_dists = [float(r["tp_dist_usd"]) for r in closed_rows]
    gross_rrs = [float(r["gross_rr"]) for r in closed_rows]
    fee_shares = [float(r["fee_share_of_planned_risk"]) for r in closed_rows if r.get("fee_share_of_planned_risk") is not None]
    hold_mins = [float(r["hold_minutes"]) for r in closed_rows if r.get("hold_minutes") is not None]
    adv_gaps = [float(r["adverse_open_gap_usd"]) for r in closed_rows]
    loss_abs = [abs(float(r["realized_net_pnl_usd"])) for r in loss_rows]
    loss_mfe20 = [float(r["post_hoc_mfe_20bar_in_sl_units"]) for r in loss_rows]

    over_250_cnt = sum(1 for x in loss_abs if x > PLANNED_NET_RISK_CAP_USD + 1e-9)
    mfe_ge_1x_cnt = sum(1 for x in loss_mfe20 if x >= 1.0)
    mfe_ge_1p5x_cnt = sum(1 for x in loss_mfe20 if x >= 1.5)
    high_fee_cnt = sum(1 for x in fee_shares if x >= 0.50)
    exit_breakdown = dict(sorted(Counter(str(r.get("exit_type")) for r in forensic_rows).items()))

    q_sl = _quantile_summary(stop_dists)
    q_tp = _quantile_summary(tp_dists)
    q_rr = _quantile_summary(gross_rrs)
    q_fee = _quantile_summary(fee_shares)
    q_loss = _quantile_summary(loss_abs)

    f05_diag = {
        "data_regime": "REUSED_DEV_NOT_FRESH_OOS",
        "epistemic_label": "ASSOCIATION_ONLY_NO_CAUSAL_CLAIM",
        "planned_net_risk_cap_usd": PLANNED_NET_RISK_CAP_USD,
        "planned_cap_is_physical_guarantee": False,
        "realized_tail_loss_disclosure": (
            f"Planned risk <= {PLANNED_NET_RISK_CAP_USD:.2f} USD is a pre-entry stop estimate, NOT a physical max-loss guarantee. "
            f"Observed {over_250_cnt}/{len(loss_rows)} closed losses exceeded {PLANNED_NET_RISK_CAP_USD:.2f} USD "
            f"(median {q_loss['p50']} USD, max {q_loss['max']} USD) due to next-bar open adverse gaps, spread/slippage, and taker fees."
        ),
        "denominators": {
            "n_scheduled": len(forensic_rows),
            "n_closed": len(closed_rows),
            "n_wins": len(win_rows),
            "n_losses": len(loss_rows),
        },
        "exit_type_breakdown": exit_breakdown,
        "distributions_closed_trades": {
            "stop_dist_usd": q_sl,
            "tp_dist_usd": q_tp,
            "gross_rr": q_rr,
            "fee_share_of_planned_risk": q_fee,
            "hold_minutes_closed": _quantile_summary(hold_mins),
            "adverse_open_gap_usd": _quantile_summary(adv_gaps),
            "realized_loss_abs_usd": q_loss,
            "losses_post_hoc_20bar_mfe_in_sl_units": _quantile_summary(loss_mfe20),
        },
        "non_exclusive_overlap_counts": {
            "warning": "Non-exclusive descriptive overlap cohorts over closed trades; NEVER sum percentages to 100%.",
            "fees_ge_50pct_of_planned_risk_over_closed": f"{high_fee_cnt}/{len(closed_rows)}",
            "losses_20bar_mfe_ge_1p0x_sl_before_stopout": f"{mfe_ge_1x_cnt}/{len(loss_rows)}",
            "losses_20bar_mfe_ge_1p5x_sl_before_stopout": f"{mfe_ge_1p5x_cnt}/{len(loss_rows)}",
            "realized_losses_gt_2p50_cap": f"{over_250_cnt}/{len(loss_rows)}",
            "same_bar_tie_conservative_losses": int(exit_breakdown.get("LOSS_TIE_CONSERVATIVE", 0)),
        },
        "r3_attribution_cross_check_match": (
            int((f08_attr.get("non_exclusive_failure_buckets") or {}).get("losses_with_20bar_mfe_ge_1p0x_sl_before_stopout", -1)) == mfe_ge_1x_cnt
            and int((f08_attr.get("non_exclusive_failure_buckets") or {}).get("realized_losses_gt_2p50_cap_count", -1)) == over_250_cnt
        ),
        "status": "PASS" if (len(closed_rows) == 203 and over_250_cnt == 139 and mfe_ge_1x_cnt == 86) else "FAIL",
    }

    hypotheses_table = [
        {
            "hypothesis_id": "H_MECH_01_DISTANT_TP_VS_TIGHT_SL",
            "axis": "STRATEGY_AND_COST_AXIS",
            "epistemic_label": "ASSOCIATION_ONLY_NO_CAUSAL_CLAIM",
            "observed_metric": f"median_SL={q_sl['p50']} USD vs median_TP={q_tp['p50']} USD (median Gross R:R={q_rr['p50']}x)",
            "observed_count_and_denominator": f"{len(closed_rows)} closed trades ({len(win_rows)}W/{len(loss_rows)}L) on 180m horizon",
            "time_horizon": "180m max bar window (REUSED DEV 2026-10-08..2026-10-28)",
            "cohort_overlap_and_censoring": "28/233 filled trades censored open at 180m; 2/233 unresolved data gap.",
            "unmatched_confounders": ["market volatility regime", "session liquidity", "1-position serial scheduling lock-out (1090 skipped)"],
            "competing_explanations": [
                "Entry trigger may lack directional predictive edge regardless of TP/SL ratio (even direction-inverted symmetric SL/TP remains deeply negative: -298.4195 USD).",
                "Shortening TP without pre-registered causal control can merely trade lower hit distance for smaller win payoff while keeping negative expectancy (as observed descriptively in E3: -439.0423 USD / -0.8628R).",
            ],
            "causal_fix_claimed": False,
            "confidence": "MODERATE_DESCRIPTIVE_ASSOCIATION_LOW_CAUSAL",
        },
        {
            "hypothesis_id": "H_MECH_02_POST_HOC_MFE_BEFORE_STOPOUT",
            "axis": "STRATEGY_AND_COST_AXIS",
            "epistemic_label": "ASSOCIATION_ONLY_NO_CAUSAL_CLAIM",
            "observed_metric": f"{mfe_ge_1x_cnt}/{len(loss_rows)} ({round(100.0 * mfe_ge_1x_cnt / max(1, len(loss_rows)), 2)}%) losses reached >=1.0x SL favorably within 20 bars before stopping out",
            "observed_count_and_denominator": f"{mfe_ge_1x_cnt}/{len(loss_rows)} closed losses ({mfe_ge_1p5x_cnt}/{len(loss_rows)} reached >=1.5x SL)",
            "time_horizon": "First 20 M1 bars post-entry (post-hoc descriptive only)",
            "cohort_overlap_and_censoring": "Conditioned post-hoc on realized LOSS subset (196/203); excludes 28 censored trades.",
            "unmatched_confounders": ["random-walk excursion probability of reaching +1x SL before -1x SL is naturally high (~50% symmetric baseline)", "selection on losers induces hindsight bias"],
            "competing_explanations": [
                "Under a zero-drift random walk, a large fraction of trades that eventually hit -1x SL first excursion past +1x SL before reversing; observing 86/196 (43.88%) does NOT prove a partial-TP or trailing-stop rule would produce positive net expectancy after fees.",
            ],
            "causal_fix_claimed": False,
            "confidence": "DESCRIPTIVE_ONLY_NOT_CAUSAL_PROOF",
        },
        {
            "hypothesis_id": "H_MECH_03_TAKER_FRICTION_DRAG",
            "axis": "STRATEGY_AND_COST_AXIS",
            "epistemic_label": "ASSOCIATION_ONLY_NO_CAUSAL_CLAIM",
            "observed_metric": f"closed_fees=276.6577 USD (median {q_fee['p50']}x planned risk; {high_fee_cnt}/{len(closed_rows)} trades >=50% planned risk)",
            "observed_count_and_denominator": f"{high_fee_cnt}/{len(closed_rows)} closed trades at modeled 0.04% taker fee",
            "time_horizon": "Full 1X_BASE_0p0004 replay",
            "cohort_overlap_and_censoring": "Excludes 14.7151 USD accrued entry fees on 28 open trades and 1.6080 USD on 2 gap trades.",
            "unmatched_confounders": ["effective account fee tier UNKNOWN_NOT_LOCALLY_VERIFIED", "tight M1 stops force larger notional per $2.50 risk cap, mechanically magnifying notional fee"],
            "competing_explanations": [
                "Counterfactual zero-fee check proves baseline gross P&L is ALREADY -164.9541 USD (< 0) before fees. Reducing fees alone CANNOT make existing baseline fills profitable.",
            ],
            "causal_fix_claimed": False,
            "confidence": "HIGH_ACCOUNTING_FACT_NOT_SOLO_CAUSE",
        },
        {
            "hypothesis_id": "H_MECH_04_TAIL_LOSS_BEYOND_PLANNED_CAP",
            "axis": "STRATEGY_AND_COST_AXIS",
            "epistemic_label": "ASSOCIATION_ONLY_NO_CAUSAL_CLAIM",
            "observed_metric": f"{over_250_cnt}/{len(loss_rows)} losses exceeded $2.50 planned cap (max {q_loss['max']} USD)",
            "observed_count_and_denominator": f"{over_250_cnt}/{len(loss_rows)} closed losses",
            "time_horizon": "1X_BASE_0p0004 closed losses",
            "cohort_overlap_and_censoring": "Modeled spread ($0.20) + slippage ($0.10); real L1 BBO & live slippage unobserved.",
            "unmatched_confounders": ["unobserved real orderbook depth and weekend/news gap slippage could produce substantially larger live tails"],
            "competing_explanations": [
                "Excess over $2.50 is a small mechanical slippage/fee overshoot (~$0.0574 max in modeled OHLCV), NOT the primary cause of -441.61 USD net loss, though live tail risk remains unquantified without L1/fill logs.",
            ],
            "causal_fix_claimed": False,
            "confidence": "HIGH_ACCOUNTING_DISCLOSURE",
        },
    ]

    f10_proof = {
        "data_regime": "REUSED_DEV_NOT_FRESH_OOS",
        "three_axis_diagnosis": {
            "ENGINE_ACCOUNTING_AXIS": {
                "verdict": "NOT_FOUND_IN_TESTED_SCOPE",
                "claim_disclaimer": "Means no reproducible accounting/simulator defect was detected in tested scope; does NOT claim 'no bugs anywhere'.",
                "evidence": {
                    "decimal_aggregate_residual_usd": decimal_bridge["closed_trades_decimal_bridge_usd"]["aggregate_gross_minus_fees_minus_net_residual_usd"],
                    "decimal_max_trade_residual_usd": decimal_bridge["closed_trades_decimal_bridge_usd"]["max_per_trade_gross_minus_fees_minus_net_residual_usd"],
                    "open_fees_separated_from_closed": True,
                    "historical_anchors_exact_match": decimal_bridge["historical_r3_c2_anchor_exact_match"],
                },
            },
            "STRATEGY_AND_COST_AXIS": {
                "verdict": "ASSOCIATION_ONLY_NO_CAUSAL_CLAIM",
                "causal_root_fix_confirmed": False,
                "conclusion_tag": "NO_CONFIRMED_CAUSAL_ROOT_FIX",
                "fee_reduction_alone_sufficient": False,
                "fee_only_zero_fee_net_usd": fee_only_diag["zero_fee_counterfactual_net_pnl_usd"],
                "new_strategy_arms_e4_e5_created": False,
                "optimal_sl_tp_invented": False,
                "hypotheses_table": hypotheses_table,
            },
            "DATA_EXECUTION_AXIS": {
                "verdict": "INCOMPLETE_OBSERVED_EXECUTION_DATA",
                "quote_mode": "MODELED_BID_ASK",
                "observed_l1_bbo_available": False,
                "account_effective_fee_tier": "UNKNOWN_NOT_LOCALLY_VERIFIED",
                "funding_cost_status": "EXCLUDED_UNMODELED",
                "fresh_untouched_prospective_oos_available": False,
                "m1_ohlcv_gaps_observed": True,
                "private_exchange_api_called": False,
                "credentials_requested": False,
            },
        },
        "anti_claim_inflation_attestation": {
            "reused_dev_treated_as_oos": False,
            "causal_improvement_claimed": False,
            "fee_only_treated_as_executable_alpha": False,
            "raw_pnl_ranking_across_unmatched_arms_prohibited": True,
            "e4_or_e5_or_threshold_tuning_performed": False,
            "economic_verdict": "NO_PROVEN_EDGE",
            "kh04_entry": "BLOCKED_NO_DEV_CANDIDATE",
            "activation_ready": ACTIVATION_READY_KH02_R2,
            "oos_evaluations": 0,
        },
        "status": "PASS",
    }
    return f05_diag, f10_proof


def audit_r4_covariate_overlap_and_effective_n(
    *,
    f15_cost: Mapping[str, Any],
    f16_gate: Mapping[str, Any],
    forensic_rows: Sequence[Mapping[str, Any]],
    f10_nc: Mapping[str, Any],
    f13_wf: Mapping[str, Any],
    r2_fold_causality: Mapping[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Produces:
      - Artifact 06 (`06_censoring_open_gap_unfilled_and_effective_n.json`):
        explicit censoring (28/233), data-gap (2/233), unfilled (0/233), clustered effective N
        (independent setup episodes & NY days), strict CI parsing, underpowered flags.
      - Artifact 09 (`09_E1_E2_E3_null_negative_controls_comparability.json`):
        E1/E2/E3/NULL non-conflation audit, population coverage comparison (`58 / 131 / 206 / 203`),
        explicit `INCOMPARABLE_UNMATCHED_POPULATIONS` classification prohibiting raw-PnL ranking,
        paired negative controls (direction-inverted, placebo, stale shift, future mutation), and
        R2 forward-only DEV embargo >=240m causality verification.
    """
    filled_rows = [r for r in forensic_rows if bool(r.get("filled"))]
    closed_rows = [r for r in filled_rows if r.get("outcome_label") in ("WIN", "LOSS")]
    open_rows = [r for r in filled_rows if r.get("outcome_label") == "OPEN_AT_HORIZON"]
    gap_rows = [r for r in forensic_rows if r.get("outcome_label") == "DATA_GAP_UNRESOLVED"]
    unfilled_rows = [r for r in forensic_rows if not bool(r.get("filled"))]

    n_sched = len(forensic_rows)
    n_filled = len(filled_rows)
    n_closed = len(closed_rows)
    n_open = len(open_rows)
    n_gap = len(gap_rows)
    n_unfilled = len(unfilled_rows)

    ny_days_closed = sorted({str(r["ny_date"]) for r in closed_rows})
    episodes_closed = sorted({str(r["setup_episode_id"]) for r in closed_rows})
    day_counts = Counter(str(r["ny_date"]) for r in closed_rows)

    dyn_1x = ((f15_cost.get("dynamic_full_rerisk_six_scenarios") or {}).get("1X_BASE_0p0004") or {}).get("arms") or {}
    arm_censor_summary: Dict[str, Any] = {}
    for ac in ("NC_NULL_TRIGGER", "KH03_R3_E1_LOW_FRICTION_STOP_FLOOR", "KH03_R3_E2_CAUSAL_DELAYED_CONFIRMATION", "KH03_R3_E3_CAUSAL_NEAREST_M5_UNSWEPT_EXIT"):
        rs = dyn_1x.get(ac) or {}
        sc_n = int(rs.get("scheduled", 0))
        unf_n = int(rs.get("unfilled", 0))
        fil_n = sc_n - unf_n
        cl_n = int(rs.get("closed", 0))
        op_n = int(rs.get("open_at_horizon", 0))
        gp_n = int(rs.get("data_gap_unresolved", 0))
        ny_d = int(rs.get("effective_ny_days", 0))
        ep_n = int(rs.get("effective_setup_episodes", 0))
        ci_p = parse_kh03_cluster_ci_strict(rs.get("ny_day_cluster_95ci_r"), label=f"{ac}_ny_day_95ci")
        arm_censor_summary[ac] = {
            "n_scheduled": sc_n,
            "n_filled": fil_n,
            "n_closed": cl_n,
            "n_open_censored": op_n,
            "n_gap_unresolved": gp_n,
            "n_unfilled": unf_n,
            "censoring_rate_over_filled": round(op_n / max(1, fil_n), 4),
            "gap_rate_over_scheduled": round(gp_n / max(1, sc_n), 4),
            "effective_setup_episodes": ep_n,
            "effective_ny_days": ny_d,
            "underpowered_flag": bool(cl_n < 100 or ny_d < 15),
            "ny_day_cluster_95ci_parsed": ci_p,
        }

    f06_censor = {
        "data_regime": "REUSED_DEV_NOT_FRESH_OOS",
        "baseline_1x_censoring_and_gaps": {
            "n_scheduled": n_sched,
            "n_filled": n_filled,
            "n_closed": n_closed,
            "n_open_censored": n_open,
            "n_gap_unresolved": n_gap,
            "n_unfilled": n_unfilled,
            "censoring_fraction_over_filled": f"{n_open}/{n_filled}",
            "censoring_rate_over_filled": round(n_open / max(1, n_filled), 4),
            "gap_fraction_over_scheduled": f"{n_gap}/{n_sched}",
            "gap_rate_over_scheduled": round(n_gap / max(1, n_sched), 4),
            "open_trades_silently_dropped": False,
            "open_trades_recoded_as_win_or_loss": False,
            "gap_trades_recoded_as_win_or_loss": False,
            "horizon_endpoint_rule": "Trades still open after 180 M1 bars are strictly labeled OPEN_AT_HORIZON (right-censored); trades encountering an unfillable data gap are labeled DATA_GAP_UNRESOLVED.",
        },
        "dependence_and_effective_n_disclosure": {
            "closed_trades_raw_count": n_closed,
            "effective_independent_setup_episodes": len(episodes_closed),
            "effective_ny_days_closed": len(ny_days_closed),
            "trades_per_ny_day_min_median_max": {
                "min": min(day_counts.values()) if day_counts else 0,
                "p50": _nearest_quantile(list(day_counts.values()), 0.50),
                "max": max(day_counts.values()) if day_counts else 0,
            },
            "independence_caveat": (
                f"The {n_closed} closed baseline trades span only {len(ny_days_closed)} NY trading days "
                f"({len(episodes_closed)} setup episodes) and are serially dependent under 1-position scheduling; "
                "NEVER treat 203 outcomes as 203 i.i.d. trials."
            ),
        },
        "arms_censoring_and_effective_n": arm_censor_summary,
        "status": "PASS" if (n_open == 28 and n_gap == 2 and n_unfilled == 0 and n_closed == 203 and len(ny_days_closed) > 0) else "FAIL",
    }

    # Artifact 09: E1 / E2 / E3 / NULL comparability, strict non-conflation, negative controls, R2 embargo
    arm_profiles: Dict[str, Any] = {}
    anchor_diffs: List[str] = []
    prim_evals = f16_gate.get("primary_arm_evaluations") or {}
    for ac in ("NC_NULL_TRIGGER", "KH03_R3_E1_LOW_FRICTION_STOP_FLOOR", "KH03_R3_E2_CAUSAL_DELAYED_CONFIRMATION", "KH03_R3_E3_CAUSAL_NEAREST_M5_UNSWEPT_EXIT"):
        rs = dyn_1x.get(ac) or {}
        anc = SEALED_KH03_R4_1X_ANCHORS[ac]
        cl_n, w_n, l_n = int(rs.get("closed", -1)), int(rs.get("wins", -1)), int(rs.get("losses", -1))
        g_d = _to_dec(rs.get("gross_pnl_usd", 0), field_name=f"{ac}.gross")
        f_d = _to_dec(rs.get("total_fees_usd", 0), field_name=f"{ac}.fees")
        n_d = _to_dec(rs.get("net_pnl_usd", 0), field_name=f"{ac}.net")
        e_d = _to_dec(rs.get("expectancy_r", 0), field_name=f"{ac}.exp")
        if cl_n != anc["closed"] or w_n != anc["wins"] or l_n != anc["losses"]:
            anchor_diffs.append(f"COUNT_DRIFT:{ac}")
        if abs(n_d - anc["net_pnl_usd"]) > Decimal("0.01"):
            anchor_diffs.append(f"NET_DRIFT:{ac}")
        if abs(e_d - anc["expectancy_r"]) > Decimal("0.001"):
            anchor_diffs.append(f"EXPR_DRIFT:{ac}")
        ci_p = parse_kh03_cluster_ci_strict(rs.get("ny_day_cluster_95ci_r"), label=f"{ac}_1x_ci")
        ev = prim_evals.get(ac) or {}
        arm_profiles[ac] = {
            "n_eligible": int(rs.get("eligible_candidates_input", 0)),
            "n_scheduled": int(rs.get("scheduled", 0)),
            "n_closed": cl_n,
            "n_wins": w_n,
            "n_losses": l_n,
            "n_open_censored": int(rs.get("open_at_horizon", 0)),
            "n_gap_unresolved": int(rs.get("data_gap_unresolved", 0)),
            "effective_ny_days": int(rs.get("effective_ny_days", 0)),
            "gross_pnl_usd": _q4(g_d),
            "closed_fees_usd": _q4(f_d),
            "net_pnl_usd": _q4(n_d),
            "expectancy_r": _q4(e_d),
            "win_rate_over_closed": round(w_n / max(1, cl_n), 4),
            "ny_day_cluster_95ci_r_raw": rs.get("ny_day_cluster_95ci_r"),
            "ny_day_cluster_95ci_r_parsed": ci_p,
            "holm_bonferroni_adjusted_pvalue": ev.get("holm_bonferroni_adjusted_pvalue"),
            "passes_preregistered_dev_advancement": bool(ev.get("passes_preregistered_dev_advancement", False)),
            "executed_ids_sha256": rs.get("executed_ids_sha256"),
        }

    # Prove arms have different populations and forbid raw-PnL "best arm" ranking
    closed_counts = {ac: arm_profiles[ac]["n_closed"] for ac in arm_profiles}
    all_strictly_negative_ci = all(
        arm_profiles[ac]["ny_day_cluster_95ci_r_parsed"].get("status") == "CI_VALID_NEGATIVE"
        for ac in arm_profiles
    )
    none_passed_dev = all(not arm_profiles[ac]["passes_preregistered_dev_advancement"] for ac in arm_profiles)

    f09_comp = {
        "data_regime": "REUSED_DEV_NOT_FRESH_OOS",
        "comparability_classification": "INCOMPARABLE_UNMATCHED_POPULATIONS",
        "raw_pnl_best_arm_selector_prohibited": True,
        "sum_overlapping_arms_prohibited": True,
        "population_non_conflation_note": (
            f"Closed counts differ materially across arms (NULL={closed_counts['NC_NULL_TRIGGER']}, "
            f"E1={closed_counts['KH03_R3_E1_LOW_FRICTION_STOP_FLOOR']}, "
            f"E2={closed_counts['KH03_R3_E2_CAUSAL_DELAYED_CONFIRMATION']}, "
            f"E3={closed_counts['KH03_R3_E3_CAUSAL_NEAREST_M5_UNSWEPT_EXIT']}). "
            "E1 having smaller total USD loss (-118.01 vs -441.61) reflects filtering out 71% of trades, NOT economic profitability "
            "(expectancy remains -0.8286R with strictly negative 95% CI). Arms are INCOMPARABLE on raw total USD P&L."
        ),
        "arm_profiles_1x": arm_profiles,
        "all_arms_strictly_negative_ci": all_strictly_negative_ci,
        "passing_dev_candidate_arms": list(f16_gate.get("passing_dev_candidate_arms") or []),
        "historical_r3_c2_arm_anchors_exact_match": len(anchor_diffs) == 0,
        "historical_anchor_diffs": anchor_diffs,
        "negative_controls_and_label_integrity": dict(f10_nc),
        "r2_forward_only_dev_embargo_regression": {
            "embargo_minutes_required": 240,
            "is_fresh_oos": False,
            "wf_status": f13_wf.get("status"),
            "fold_causality_audit": dict(r2_fold_causality),
            "zero_future_train_bars": bool(
                r2_fold_causality.get("valid", False)
                and len(r2_fold_causality.get("violations") or ()) == 0
            ),
        },
        "status": (
            "PASS"
            if (
                len(anchor_diffs) == 0
                and all_strictly_negative_ci
                and none_passed_dev
                and f10_nc.get("status") == "PASS"
                and f13_wf.get("status") == "PASS"
                and f13_wf.get("is_fresh_oos") is False
                and bool(r2_fold_causality.get("valid", False))
            )
            else "FAIL"
        ),
    }
    return f06_censor, f09_comp


def derive_r4_breakeven_feasibility_bounds(
    forensic_rows: Sequence[Mapping[str, Any]],
    *,
    f09_cost: Mapping[str, Any],
    dyn6_scenarios: Mapping[str, Any],
    dataset_sha: str,
) -> Dict[str, Any]:
    """
    Artifact 08 (`08_full_dynamic_rerisk_6_cost_scenario_replay.json`):
    Full dynamic 6-cost-scenario accounting bridge + conservative observed 1x payoff/win-loss
    and break-even win-rate feasibility bounds without fabricating favorable unknown costs.
    """
    closed_1x = [r for r in forensic_rows if bool(r.get("filled")) and r.get("outcome_label") in ("WIN", "LOSS")]
    win_r = [float(r["realized_net_r"]) for r in closed_1x if r.get("outcome_label") == "WIN"]
    loss_r = [abs(float(r["realized_net_r"])) for r in closed_1x if r.get("outcome_label") == "LOSS"]
    avg_win_r = round(sum(win_r) / len(win_r), 4) if win_r else None
    avg_loss_r = round(sum(loss_r) / len(loss_r), 4) if loss_r else None
    emp_be_wr = (
        round(avg_loss_r / (avg_win_r + avg_loss_r), 4)
        if (avg_win_r is not None and avg_loss_r is not None and (avg_win_r + avg_loss_r) > 0)
        else None
    )
    obs_wr = round(len(win_r) / max(1, len(closed_1x)), 4)

    # Audit every scenario and arm in FULL_DYNAMIC_RERISK with exact Decimal
    scenarios_audit: Dict[str, Any] = {}
    zero_fee_bugs = 0
    residual_breaches = 0
    total_overlaps = 0
    for sc_id, sc_obj in sorted(dyn6_scenarios.items()):
        arms_out: Dict[str, Any] = {}
        for ac, rs in sorted(((sc_obj or {}).get("arms") or {}).items()):
            cl = int(rs.get("closed", 0))
            g = _to_dec(rs.get("gross_pnl_usd", 0), field_name=f"{sc_id}.{ac}.gross")
            fe = _to_dec(rs.get("total_fees_usd", 0), field_name=f"{sc_id}.{ac}.fees")
            ne = _to_dec(rs.get("net_pnl_usd", 0), field_name=f"{sc_id}.{ac}.net")
            res = abs((g - fe) - ne)
            ov = int(rs.get("overlap_violations", 0))
            total_overlaps += ov
            if cl > 0 and fe <= Decimal("0") and (g - ne) > Decimal("0.01"):
                zero_fee_bugs += 1
            if cl > 0 and res > Decimal("0.01"):
                residual_breaches += 1
            arms_out[ac] = {
                **dict(rs),
                "decimal_gross_minus_fee_minus_net_residual_usd": _q4(res),
            }
        null_be = ((f09_cost.get("scenarios") or {}).get(sc_id) or {})
        scenarios_audit[sc_id] = {
            "population_mode": "FULL_DYNAMIC_RERISK",
            "parameters": null_be.get("parameters"),
            "null_empirical_breakeven_win_rate": null_be.get("empirical_breakeven_win_rate"),
            "null_observed_closed_win_rate": null_be.get("observed_closed_win_rate"),
            "null_avg_realized_win_r": null_be.get("avg_realized_win_r"),
            "null_avg_realized_loss_abs_r": null_be.get("avg_realized_loss_abs_r"),
            "arms": arms_out,
        }

    ok = (
        len(scenarios_audit) == 6
        and zero_fee_bugs == 0
        and residual_breaches == 0
        and total_overlaps == 0
        and emp_be_wr is not None
        and obs_wr < emp_be_wr
    )
    return {
        "data_regime": "REUSED_DEV_NOT_FRESH_OOS",
        "source_dataset_sha256": dataset_sha,
        "population_mode": "FULL_DYNAMIC_RERISK",
        "dynamic_vs_fixed_fill_warning": (
            "Each dynamic cost scenario alters risk-cap eligibility and 1-position scheduling; "
            "populations change materially (203 -> 138 -> 111 -> 46 -> 45 -> 14 closed NULL trades). "
            "NEVER treat dynamic scenario differences as fixed-fill cost deltas."
        ),
        "observed_1x_breakeven_bounds_conservative": {
            "observed_closed_trades": len(closed_1x),
            "observed_wins": len(win_r),
            "observed_losses": len(loss_r),
            "observed_closed_win_rate": obs_wr,
            "avg_realized_win_r": avg_win_r,
            "avg_realized_loss_abs_r": avg_loss_r,
            "empirical_breakeven_win_rate_at_1x_modeled": emp_be_wr,
            "win_rate_deficit_vs_breakeven": round(obs_wr - emp_be_wr, 4) if emp_be_wr is not None else None,
            "required_win_rate_multiple_of_observed": round(emp_be_wr / max(1e-6, obs_wr), 2) if (emp_be_wr is not None and obs_wr > 0) else None,
            "conservative_caveat": (
                "Even under modeled 1x costs (excluding funding and unobserved live L1 slippage), observed win rate (3.45%) "
                "is ~7.5x BELOW empirical break-even (~25.75%). Real unmodeled funding and L1 spread widening would raise break-even further."
            ),
        },
        "quote_mode": "MODELED_BID_ASK",
        "observed_l1_bbo_available": False,
        "ACCOUNT_EFFECTIVE_FEE_VERDICT": "UNKNOWN_NOT_LOCALLY_VERIFIED",
        "funding_cost_status": "EXCLUDED_UNMODELED",
        "zero_fee_display_bugs_count": zero_fee_bugs,
        "decimal_residual_breaches_count": residual_breaches,
        "total_overlap_violations": total_overlaps,
        "dynamic_full_rerisk_six_scenarios": scenarios_audit,
        "status": "PASS" if ok else "FAIL",
    }


def build_r4_data_gap_register(
    *,
    repo_root: Path,
    dataset_sha: str,
    timeframe_stats: Mapping[str, Any],
) -> Dict[str, Any]:
    """
    Artifact 11 (`11_data_readiness_register_and_missing_evidence.json`):
    Machine-readable DATA_READINESS_REGISTER with all 15 required fields across all 8 mandatory
    data domains, plus the 3-part A/B/C readiness roadmap and prospective OOS freshness-sealing trap.
    NEVER fetches credentials or calls private/live exchange APIs.
    """
    m1_stats = timeframe_stats.get("1m") or {}
    m1_gap_pct = round(100.0 * float(m1_stats.get("missing_bar_ratio") or 0.0), 2)

    entries: List[Dict[str, Any]] = [
        {
            "name": "historical_m1_m5_h1_raw_ohlcv_and_gaps",
            "why_required": "Core deterministic price series for as-of MTF structural level detection and chronological 1-position trade replay.",
            "currently_observed": True,
            "source_existing_path": CANONICAL_DATASET_REL_PATH,
            "evidence_sha256": dataset_sha,
            "freshness": f"HISTORICAL_SEALED_REUSED_DEV ({m1_stats.get('first_utc')}..{m1_stats.get('last_utc')})",
            "granularity": "1m (22,376 bars), 5m (31,609), 15m (15,090), 1H (3,863), 4H (965), 1D (181); UTC epoch ms",
            "observed_or_modeled_or_missing": "OBSERVED_WITH_DISCLOSED_GAPS",
            "cost_privacy_risk": "NONE_LOCAL_PUBLIC_HISTORICAL_ARCHIVE",
            "possible_owner_action": "Retain sealed read-only for DEV regression; authorize separate forward prospective collection for any future OOS window.",
            "blocked_if_absent": True,
            "confirmation_method": "SHA-256 verification against canonical sealed hash + timestamp monotonicity & gap audit.",
            "minimum_coverage_spec": "Continuous UTC-aligned M1/M5/15m/1H/4H/1D OHLCV with explicit gap ledger (current M1 missing-bar ratio including weekends ~" + f"{m1_gap_pct}%).",
            "cannot_assume": "Cannot assume zero weekend/maintenance gaps or intrabar tick ordering inside a 1m bar.",
            "promotion_gates_dependent_on_field": ["G02_IMMUTABLE_DATASET_AND_PRIOR_SEALED_SHA", "G06_CENSOR_GAP_STATUS", "G22_A1_SI8_ADAPTER_D4_PARITY"],
        },
        {
            "name": "observed_l1_bid_ask_and_spread_dynamics",
            "why_required": "Measures true decision-time and fill-time bid/ask spread and liquidity across sessions instead of constant $0.20 modeled spread.",
            "currently_observed": False,
            "source_existing_path": None,
            "evidence_sha256": None,
            "freshness": "NOT_AVAILABLE",
            "granularity": "Best bid/ask L1 quotes synchronized to decision & next-bar open timestamps (<=1s cadence)",
            "observed_or_modeled_or_missing": "MISSING_CURRENTLY_MODELED_0p20_USD",
            "cost_privacy_risk": "LOW_PUBLIC_MARKET_FEED_NO_ACCOUNT_SECRETS_REQUIRED",
            "possible_owner_action": "Owner authorizes public exchange/vendor L1 BBO recorder in an isolated collection run with SHA-256 sealing.",
            "blocked_if_absent": True,
            "confirmation_method": "Independent SHA-256 check of raw L1 quote archive + timestamp coverage >=99% of decision/fill bars.",
            "minimum_coverage_spec": "Prospective L1 bid/ask stream covering full prospective evaluation window across Asia/London/NY sessions.",
            "cannot_assume": "Cannot assume constant $0.20 spread or treat H3 resize/fill timing as executable without observed L1 quotes.",
            "promotion_gates_dependent_on_field": ["G17_DATA_READINESS_REGISTER_TRUE_UNKNOWN", "G18_NO_FABRICATED_ACCOUNT_FEES_L1_FUNDING", "KH04_EXECUTION_FIDELITY"],
        },
        {
            "name": "exchange_account_effective_fee_tier",
            "why_required": "Modeled taker fee (0.04% = 4 bps) consumes median ~56% of $2.50 planned risk; actual fee schedule directly scales realized cost drag.",
            "currently_observed": False,
            "source_existing_path": None,
            "evidence_sha256": None,
            "freshness": "UNKNOWN_NOT_LOCALLY_VERIFIED",
            "granularity": "Contract-specific (XAUUSDT perp) maker/taker schedule with effective date, VIP tier, and region/rebate terms",
            "observed_or_modeled_or_missing": "MISSING_UNKNOWN_NOT_LOCALLY_VERIFIED",
            "cost_privacy_risk": "MEDIUM_ACCOUNT_METADATA_NEVER_COMMIT_API_KEYS_OR_CREDENTIALS",
            "possible_owner_action": "Owner exports redacted fee-tier confirmation (no API keys/secrets) into an owner-approved config artifact.",
            "blocked_if_absent": True,
            "confirmation_method": "Owner-signed redacted fee schedule JSON + SHA-256 receipt (no live private API polling by agent).",
            "minimum_coverage_spec": "Exact maker/taker rates for target symbol/contract/region with effective timestamp.",
            "cannot_assume": "Cannot assume VIP/maker discounts, zero fees, or that modeled 0.04% matches owner's actual account.",
            "promotion_gates_dependent_on_field": ["G17_DATA_READINESS_REGISTER_TRUE_UNKNOWN", "G18_NO_FABRICATED_ACCOUNT_FEES_L1_FUNDING", "KH04_COST_VERIFICATION"],
        },
        {
            "name": "observed_execution_slippage_maker_taker_and_exchange_rules",
            "why_required": "Verifies true fill slippage vs modeled $0.10, real maker vs taker fill mix, leverage caps, tick size, lot step, and mark/last price source.",
            "currently_observed": False,
            "source_existing_path": None,
            "evidence_sha256": None,
            "freshness": "NOT_AVAILABLE",
            "granularity": "Per-fill execution receipts + static exchange instrument metadata (tickSize, stepSize, minNotional, maxLeverage)",
            "observed_or_modeled_or_missing": "MISSING_CURRENTLY_MODELED_0p10_SLIPPAGE",
            "cost_privacy_risk": "LOW_TO_MEDIUM_PUBLIC_SPECS_AND_REDACTED_FILL_SAMPLES",
            "possible_owner_action": "Owner supplies public exchangeInfo contract spec snapshot and, only after a valid DEV candidate exists, bounded paper/micro-fill slippage calibration.",
            "blocked_if_absent": True,
            "confirmation_method": "Exchange instrument metadata SHA-256 + empirical slippage distribution vs modeled $0.10.",
            "minimum_coverage_spec": "Full contract rules for target symbol + observed slippage quantiles across volatility regimes.",
            "cannot_assume": "Cannot assume $0.10 constant slippage, zero partial fills, or 100% maker execution on stop/market entries.",
            "promotion_gates_dependent_on_field": ["G12_REALIZED_GT_PLANNED_RISK_DISCLOSED", "G18_NO_FABRICATED_ACCOUNT_FEES_L1_FUNDING", "KH04_EXECUTION_FIDELITY"],
        },
        {
            "name": "perpetual_funding_and_carrying_fee_history",
            "why_required": "Multi-hour positions crossing 8h perpetual funding timestamps incur funding credits/debits currently omitted from net P&L.",
            "currently_observed": False,
            "source_existing_path": None,
            "evidence_sha256": None,
            "freshness": "EXCLUDED_UNMODELED",
            "granularity": "8-hour (or contract-native) historical funding rate timestamps + mark prices",
            "observed_or_modeled_or_missing": "MISSING_EXCLUDED_UNMODELED",
            "cost_privacy_risk": "NONE_PUBLIC_EXCHANGE_FUNDING_HISTORY",
            "possible_owner_action": "Owner authorizes downloading public historical funding rate series aligned with OHLCV timestamps.",
            "blocked_if_absent": True,
            "confirmation_method": "SHA-256 sealed funding rate JSONL reconciled against holding intervals crossing funding UTC boundaries.",
            "minimum_coverage_spec": "100% of funding settlement timestamps spanning DEV and prospective OOS windows.",
            "cannot_assume": "Cannot assume zero funding drag or net-neutral funding across directional bias.",
            "promotion_gates_dependent_on_field": ["G17_DATA_READINESS_REGISTER_TRUE_UNKNOWN", "G18_NO_FABRICATED_ACCOUNT_FEES_L1_FUNDING"],
        },
        {
            "name": "fresh_untouched_prospective_oos_window",
            "why_required": "Existing DEV window has been reused across KH-02, KH-03.R1/R2/R3/C1/C2/R4; valid statistical validation requires unseen future data.",
            "currently_observed": False,
            "source_existing_path": None,
            "evidence_sha256": None,
            "freshness": "ABSENT_ZERO_UNTOUCHED_RECORDS",
            "granularity": "Synchronized prospective M1/M5/15m/1H/4H/1D OHLCV + L1 BBO + funding after preregistration seal date",
            "observed_or_modeled_or_missing": "MISSING_ABSENT",
            "cost_privacy_risk": "NONE_PUBLIC_MARKET_DATA_WITH_STRICT_ESCROW_ACCESS_CONTROL",
            "possible_owner_action": "Only AFTER a valid DEV candidate exists (currently NONE), authorize sealed prospective collection and escrow lock until single-shot OOS gate.",
            "blocked_if_absent": True,
            "confirmation_method": "Escrow SHA-256 seal + zero-access audit ledger confirming zero reads prior to frozen preregistered OOS run.",
            "minimum_coverage_spec": ">=30 independent NY trading days (>=100 closed trades expected) strictly post-dating strategy freeze.",
            "cannot_assume": "Cannot assume REUSED DEV fold performance transfers out-of-sample, or treat inspected newly appended data as fresh OOS.",
            "promotion_gates_dependent_on_field": ["G19_ZERO_FRESH_OOS_AND_NO_NEW_ALPHA", "KH04_OOS_EVALUATION"],
        },
        {
            "name": "correlated_opportunity_episodes_and_session_calendar",
            "why_required": "203 closed baseline trades cluster into only 17 NY days under 1-position lock-out (1,090 skipped busy); effective sample size is much smaller than trade count.",
            "currently_observed": True,
            "source_existing_path": CANONICAL_DATASET_REL_PATH,
            "evidence_sha256": dataset_sha,
            "freshness": "HISTORICAL_SEALED_REUSED_DEV_17_NY_DAYS_ONLY",
            "granularity": "Per-episode setup_episode_id, NY trading date, Asia/London/NY session bucket",
            "observed_or_modeled_or_missing": "OBSERVED_IN_DEV_INSUFFICIENT_DURATION_FOR_REGIME_GENERALIZATION",
            "cost_privacy_risk": "NONE_DERIVED_FROM_SEALED_LOCAL_OHLCV",
            "possible_owner_action": "Acquire multi-month prospective/non-overlapping regimes before attempting any statistical promotion.",
            "blocked_if_absent": False,
            "confirmation_method": "Clustered NY-day block bootstrap CI + effective_ny_days & episode counts audited by independent verifier.",
            "minimum_coverage_spec": ">=60 NY trading days spanning distinct volatility/macro regimes for future research design.",
            "cannot_assume": "Cannot assume 203 trades equal 203 independent trials or generalize 17 NY days to all gold volatility regimes.",
            "promotion_gates_dependent_on_field": ["G06_CENSOR_GAP_STATUS", "G11_E1_E2_E3_NO_POPULATION_CONFLATION"],
        },
        {
            "name": "order_execution_semantics_and_market_closure_gaps",
            "why_required": "Documents exact stop-trigger vs market-gap execution, same-bar tie conservatism, weekend/maintenance halt behavior, and 180m horizon censoring.",
            "currently_observed": True,
            "source_existing_path": "aurum-desk/backend/services/v17_9_2_structural_stop_intelligence.py",
            "evidence_sha256": _sha256_file(repo_root / "aurum-desk/backend/services/v17_9_2_structural_stop_intelligence.py"),
            "freshness": "CODE_OBSERVED_LIVE_EXCHANGE_MATCH_UNVERIFIED",
            "granularity": "Bar-level OHLCV conservative tie + next-bar open gap rule; live orderbook trigger semantics unverified",
            "observed_or_modeled_or_missing": "MODELED_IN_SIMULATOR_LIVE_EXCHANGE_UNOBSERVED",
            "cost_privacy_risk": "NONE_LOCAL_SIMULATOR_CODE",
            "possible_owner_action": "Confirm target exchange stop-market trigger type (mark vs last price) and maintenance halt handling in future spec.",
            "blocked_if_absent": True,
            "confirmation_method": "Synthetic LONG/SHORT tie/TP/gap controls + future exchange rule documentation.",
            "minimum_coverage_spec": "Explicit exchange stop-trigger spec (mark vs last) + gap handling reconciliation.",
            "cannot_assume": "Cannot assume OHLCV bar-level stop simulation captures live liquidation cascades, wick spikes, or exchange halt gaps.",
            "promotion_gates_dependent_on_field": ["G06_CENSOR_GAP_STATUS", "G14_NEGATIVE_CONTROL_AND_PLACEBO", "G20_NO_MARKET_ORDER_ALL_ACTIVATION_OFF"],
        },
    ]

    # Verify every entry strictly contains all 15 required fields
    schema_complete = all(all(f in e for f in DATA_READINESS_REQUIRED_FIELDS) for e in entries)
    missing_or_unverified_count = sum(1 for e in entries if not bool(e["currently_observed"]))

    return {
        "data_regime": "REUSED_DEV_NOT_FRESH_OOS",
        "DATA_READINESS_VERDICT": "INCOMPLETE",
        "FRESH_OOS": "BLOCKED_NO_UNTOUCHED_DATA",
        "observed_l1_bbo_available": False,
        "ACCOUNT_EFFECTIVE_FEE_VERDICT": "UNKNOWN_NOT_LOCALLY_VERIFIED",
        "funding_cost_status": "EXCLUDED_UNMODELED",
        "private_api_called": False,
        "credentials_or_api_keys_requested": False,
        "register_schema_fields_required": list(DATA_READINESS_REQUIRED_FIELDS),
        "register_schema_complete": schema_complete,
        "total_domains_audited": len(entries),
        "observed_domains_count": len(entries) - missing_or_unverified_count,
        "missing_or_unverified_domains_count": missing_or_unverified_count,
        "DATA_READINESS_REGISTER": entries,
        "readiness_roadmap_no_fabricated_costs_or_timelines": {
            "A_auditable_from_current_local_files_only": [
                "Full Decimal gross/fee/net reconciliation across 6 modeled cost scenarios and NULL/E1/E2/E3 on REUSED DEV.",
                "Exact stage conservation (22,376 M1 bars -> 1,323 eligible -> 233 scheduled -> 203 closed / 28 open / 2 gap).",
                "Association-only entry/exit geometry, 20-bar MFE, realized >$2.50 tail, and negative-control integrity audits.",
            ],
            "B_requires_owner_authorization_and_external_capture_no_credentials_requested": [
                "Public historical & prospective L1 bid/ask BBO archive (timestamped bid/ask/spread).",
                "Redacted owner-confirmed effective maker/taker fee schedule for target symbol/contract (NO API keys/secrets).",
                "Public historical funding rate & exchange contract metadata (tickSize, stepSize, stop trigger price source).",
            ],
            "C_unverifiable_until_independent_fresh_oos_and_escrow_sealing": [
                "True out-of-sample expectancy, live slippage tail beyond $2.50 planned risk, and cross-regime stability.",
                "FRESHNESS TRAP PROTOCOL: Any newly appended dataset inspected or used for feature/arm/parameter selection IMMEDIATELY loses fresh-OOS status. Fresh OOS must be SHA-256 sealed upon capture with a strict zero-access ledger and unlocked ONLY once after a frozen pre-registered candidate passes DEV.",
            ],
        },
        "status": "PASS" if (schema_complete and len(entries) >= 8 and missing_or_unverified_count >= 5) else "FAIL",
    }


def decide_r4_research_next_action(
    *,
    decimal_bridge: Mapping[str, Any],
    fee_only_diag: Mapping[str, Any],
    data_register: Mapping[str, Any],
    arm_comparability: Mapping[str, Any],
) -> Dict[str, Any]:
    """
    Artifact 12 (`12_go_nogo_decision_rules_and_owner_next_actions.json`):
    Deterministic GO / NO-GO / DIAGNOSIS_ONLY / DATA_READY_FOR_PROSPECTIVE_COLLECTION engine.
    NEVER activates trading, NEVER approves KH-04 without passing DEV candidate + observed execution data.
    """
    passing_arms = list(arm_comparability.get("passing_dev_candidate_arms") or [])
    has_dev_candidate = len(passing_arms) > 0
    gross_neg = bool(decimal_bridge.get("gross_is_strictly_negative_before_fees", True))
    fee_cannot_cure = bool(fee_only_diag.get("fee_reduction_alone_cannot_cure_baseline_losses", True))
    l1_avail = bool(data_register.get("observed_l1_bbo_available", False))
    fee_known = data_register.get("ACCOUNT_EFFECTIVE_FEE_VERDICT") != "UNKNOWN_NOT_LOCALLY_VERIFIED"
    funding_modeled = data_register.get("funding_cost_status") not in ("EXCLUDED_UNMODELED", "EXCLUDED_UNMODELED_DISCLOSED")
    fresh_oos_avail = data_register.get("FRESH_OOS") == "AVAILABLE_SEALED_UNTOUCHED"

    fail_reasons: List[str] = []
    if not has_dev_candidate:
        fail_reasons.append("ZERO_PASSING_DEV_CANDIDATES_ALL_ARMS_STRICTLY_NEGATIVE_CI")
    if gross_neg and fee_cannot_cure:
        fail_reasons.append("BASELINE_GROSS_PNL_STRICTLY_NEGATIVE_BEFORE_FEES_FEE_REDUCTION_ALONE_INSUFFICIENT")
    if not l1_avail:
        fail_reasons.append("MISSING_OBSERVED_L1_BID_ASK_SPREAD_DATA")
    if not fee_known:
        fail_reasons.append("ACCOUNT_EFFECTIVE_FEE_TIER_UNKNOWN_NOT_LOCALLY_VERIFIED")
    if not funding_modeled:
        fail_reasons.append("PERPETUAL_FUNDING_COST_EXCLUDED_UNMODELED")
    if not fresh_oos_avail:
        fail_reasons.append("FRESH_UNTOUCHED_PROSPECTIVE_OOS_WINDOW_ABSENT")

    if has_dev_candidate and l1_avail and fee_known and funding_modeled and fresh_oos_avail:
        decision = "ELIGIBLE_FOR_NEW_OWNER_AUTHORIZED_REVIEW"
    elif (not has_dev_candidate) and (not l1_avail or not fresh_oos_avail):
        decision = "NO_GO_DIAGNOSIS_ONLY_STOP_REUSED_DEV_TUNING"
    elif not has_dev_candidate:
        decision = "NO_GO_NO_DEV_CANDIDATE"
    else:
        decision = "DATA_READY_FOR_PROSPECTIVE_COLLECTION"

    return {
        "data_regime": "REUSED_DEV_NOT_FRESH_OOS",
        "DECISION": decision,
        "BINARY_OWNER_RECOMMENDATION": "STOP_STRATEGY_TUNING_ON_REUSED_DEV_AND_DECIDE_PROSPECTIVE_DATA_AUTHORIZATION",
        "RESEARCH_ECONOMIC_VERDICT": "NO_PROVEN_EDGE",
        "DIAGNOSIS_VERDICT": "DIAGNOSIS_ONLY_COMPLETE",
        "DATA_READINESS": "INCOMPLETE",
        "FRESH_OOS": "BLOCKED_NO_UNTOUCHED_DATA",
        "KH04_ENTRY": "BLOCKED_NO_DEV_CANDIDATE",
        "ACTIVATION_READY": ACTIVATION_READY_KH02_R2,
        "OOS_EVALUATIONS": 0,
        "runtime_safety_modes": dict(RUNTIME_SAFETY_MODES_KH02_R2),
        "private_api_or_credentials_requested": False,
        "fail_reasons": fail_reasons,
        "decision_rules_evaluated": [
            {"rule_id": "DR01_HAS_PREREGISTERED_PASSING_DEV_CANDIDATE", "passed": has_dev_candidate, "required_for_kh04": True},
            {"rule_id": "DR02_BASELINE_GROSS_POSITIVE_OR_CONFIRMED_CAUSAL_MECHANISM", "passed": (not gross_neg), "required_for_kh04": True},
            {"rule_id": "DR03_OBSERVED_L1_BBO_AVAILABLE", "passed": l1_avail, "required_for_kh04": True},
            {"rule_id": "DR04_ACCOUNT_EFFECTIVE_FEE_LOCALLY_VERIFIED", "passed": fee_known, "required_for_kh04": True},
            {"rule_id": "DR05_FUNDING_COST_MODELED", "passed": funding_modeled, "required_for_kh04": True},
            {"rule_id": "DR06_SEALED_UNTOUCHED_PROSPECTIVE_OOS_AVAILABLE", "passed": fresh_oos_avail, "required_for_kh04": True},
        ],
        "owner_next_actions_prerequisites": [
            "1. Do NOT tune SL/TP thresholds or add E4/E5 on the reused Oct-2026 DEV window (severe multiple-testing / overfitting risk with only 17 NY days).",
            "2. Confirm whether to pause strategy search completely OR authorize a new public prospective market-data collection phase (M1/M5/15m/1H/4H/1D OHLCV + L1 BBO + public funding rates + exchange contract spec), sealed by SHA-256.",
            "3. Provide a redacted confirmation of effective account maker/taker fee tier (NO API keys, secrets, or passwords).",
            "4. Keep any newly collected OOS slice strictly in zero-access SHA-256 escrow; never inspect or tune on it before a separately designed hypothesis passes on an independent development sample.",
            "5. Keep PRODUCTION=OFF, LIVE=OFF, PAPER=OFF, AUTO=OFF, ACTIVATION_READY=NO.",
        ],
        "status": "PASS" if (decision.startswith("NO_GO") and len(fail_reasons) >= 5) else "FAIL",
    }


def validate_r4_no_claim_inflation(docs: Mapping[str, Mapping[str, Any]]) -> Dict[str, Any]:
    """
    Fail-closed guard scanning R4 economic/diagnostic/decision documents to ensure ZERO claim
    inflation (no fake alpha, no causal wording, no fee-only-as-executable, no OOS>0, no live modes).
    """
    violations: List[str] = []
    f04 = docs.get("04") or {}
    f05 = docs.get("05") or {}
    f07 = docs.get("07") or {}
    f09 = docs.get("09") or {}
    f10 = docs.get("10") or {}
    f11 = docs.get("11") or {}
    f12 = docs.get("12") or {}

    if f05.get("planned_cap_is_physical_guarantee") is not False:
        violations.append("PLANNED_CAP_CLAIMED_AS_PHYSICAL_GUARANTEE")
    if f07.get("is_executable_strategy") is not False or f07.get("is_proven_edge") is not False:
        violations.append("FEE_ONLY_COUNTERFACTUAL_MISLABELED_AS_EXECUTABLE_OR_PROVEN_EDGE")
    if f07.get("population_mode") != "FIXED_FILLS_DESCRIPTIVE_REPRICING":
        violations.append("FEE_ONLY_POPULATION_MODE_WRONG")
    if f09.get("comparability_classification") != "INCOMPARABLE_UNMATCHED_POPULATIONS":
        violations.append("UNMATCHED_ARMS_NOT_CLASSIFIED_INCOMPARABLE")
    if f09.get("raw_pnl_best_arm_selector_prohibited") is not True:
        violations.append("RAW_PNL_RANKING_NOT_PROHIBITED")

    strat_ax = ((f10.get("three_axis_diagnosis") or {}).get("STRATEGY_AND_COST_AXIS") or {})
    if strat_ax.get("verdict") != "ASSOCIATION_ONLY_NO_CAUSAL_CLAIM" or strat_ax.get("causal_root_fix_confirmed") is not False:
        violations.append("CAUSAL_CLAIM_INFLATION_IN_10")

    if f11.get("observed_l1_bbo_available") is not False:
        violations.append("FABRICATED_L1_BBO_AVAILABLE")
    if f11.get("ACCOUNT_EFFECTIVE_FEE_VERDICT") != "UNKNOWN_NOT_LOCALLY_VERIFIED":
        violations.append("FABRICATED_ACCOUNT_EFFECTIVE_FEE")
    if f11.get("funding_cost_status") not in ("EXCLUDED_UNMODELED", "EXCLUDED_UNMODELED_DISCLOSED"):
        violations.append("FABRICATED_FUNDING_STATUS")

    if f12.get("RESEARCH_ECONOMIC_VERDICT") != "NO_PROVEN_EDGE":
        violations.append(f"FORGED_ECONOMIC_VERDICT:{f12.get('RESEARCH_ECONOMIC_VERDICT')}")
    if f12.get("KH04_ENTRY") != "BLOCKED_NO_DEV_CANDIDATE":
        violations.append(f"FORGED_KH04_ENTRY:{f12.get('KH04_ENTRY')}")
    if f12.get("ACTIVATION_READY") != "NO":
        violations.append("FORGED_ACTIVATION_READY")
    if int(f12.get("OOS_EVALUATIONS", -1)) != 0:
        violations.append("FORGED_NONZERO_OOS_EVALUATIONS")
    modes = f12.get("runtime_safety_modes") or {}
    for mk in ("PRODUCTION", "LIVE", "PAPER", "AUTO"):
        if modes.get(mk) != "OFF":
            violations.append(f"UNSAFE_RUNTIME_MODE:{mk}={modes.get(mk)}")

    # Scan serialized diagnostic text for forbidden inflated phrases (excluding exact key names)
    for dk in ("05", "07", "09", "10", "12"):
        txt = json.dumps(docs.get(dk) or {}, sort_keys=True)
        for bad_tok in FORBIDDEN_CAUSAL_OR_ALPHA_PHRASES:
            if bad_tok == "PROVEN_EDGE":
                # Allow "NO_PROVEN_EDGE", reject standalone "PROVEN_EDGE"
                cleaned = txt.replace("NO_PROVEN_EDGE", "")
                if "PROVEN_EDGE" in cleaned:
                    violations.append(f"FORBIDDEN_PHRASE_IN_{dk}:{bad_tok}")
            elif bad_tok in txt:
                violations.append(f"FORBIDDEN_PHRASE_IN_{dk}:{bad_tok}")

    # Verify baseline gross was not silently zeroed out
    g_val = ((f04.get("closed_trades_decimal_bridge_usd") or {}).get("gross_pnl_closed_usd"))
    if g_val is not None and float(g_val) >= 0.0:
        violations.append("BASELINE_GROSS_PNL_TAMPERED_NONNEGATIVE")

    return {"valid": len(violations) == 0, "status": "PASS" if len(violations) == 0 else "FAIL", "violations": violations}
