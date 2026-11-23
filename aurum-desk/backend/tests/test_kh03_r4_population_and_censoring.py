"""
aurum-desk/backend/tests/test_kh03_r4_population_and_censoring.py

Tests KH-03.R4 exact stage population conservation, 7/203 vs 7/233 denominator separation,
honest OPEN_AT_HORIZON (28) and DATA_GAP_UNRESOLVED (2) censoring handling, duplicate/overlap
rejection, clustered NY-day effective N, and E1/E2/E3/NULL INCOMPARABLE population non-conflation.
"""
from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any, Dict, List

import pytest

APP_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = APP_ROOT / "backend"
REPO_ROOT = APP_ROOT.parent
for _p in (str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.kh_02_r2_research_integrity_service import CANONICAL_DATASET_SHA256_KH02_R2
from services.kh_03_r4_economic_diagnostic_service import (
    audit_r4_covariate_overlap_and_effective_n,
    build_r4_trade_population_conservation,
)
from tests.test_kh03_r4_economic_accounting import _make_synthetic_anchor_forensic_rows


def _make_population_inputs() -> Dict[str, Any]:
    rows = _make_synthetic_anchor_forensic_rows()
    elig = [{"opportunity_id": r["opportunity_id"], "decision_timestamp_ms": r["decision_timestamp_ms"], "direction": r["direction"]} for r in rows]
    for k in range(1090):
        elig.append({"opportunity_id": f"OPP_SKIP_{k:04d}", "decision_timestamp_ms": 1791500001000 + k * 60_000, "direction": "LONG"})
    lineage_05 = {
        "stages": {
            "total_m1_bars": 22376,
            "total_directional_bar_opportunities": 44692,
            "raw_m1_triggers_count": 3788,
        },
        "conservation_checks": {"zero_1pos_overlap_violations": True},
    }
    r2_cands_1x = {"arm_candidates": {"NC_NULL_TRIGGER": elig}}
    dyn6 = {
        "1X_BASE_0p0004": {
            "arms": {
                "NC_NULL_TRIGGER": {
                    "eligible_candidates_input": 1323, "scheduled": 233, "skipped_due_to_active_position": 1090,
                    "unfilled": 0, "closed": 203, "wins": 7, "losses": 196, "open_at_horizon": 28, "data_gap_unresolved": 2,
                    "effective_setup_episodes": 198, "effective_ny_days": 17, "overlap_violations": 0,
                    "gross_pnl_usd": -164.9541, "total_fees_usd": 276.6577, "net_pnl_usd": -441.6118, "expectancy_r": -0.8806,
                    "ny_day_cluster_95ci_r": [-0.9761, -0.7712], "executed_ids_sha256": "a" * 64,
                },
                "KH03_R3_E1_LOW_FRICTION_STOP_FLOOR": {
                    "eligible_candidates_input": 200, "scheduled": 77, "skipped_due_to_active_position": 123,
                    "unfilled": 0, "closed": 58, "wins": 3, "losses": 55, "open_at_horizon": 19, "data_gap_unresolved": 0,
                    "effective_setup_episodes": 55, "effective_ny_days": 14, "overlap_violations": 0,
                    "gross_pnl_usd": -65.7135, "total_fees_usd": 52.2986, "net_pnl_usd": -118.0121, "expectancy_r": -0.8286,
                    "ny_day_cluster_95ci_r": [-0.9900, -0.6100], "executed_ids_sha256": "b" * 64,
                },
                "KH03_R3_E2_CAUSAL_DELAYED_CONFIRMATION": {
                    "eligible_candidates_input": 500, "scheduled": 160, "skipped_due_to_active_position": 340,
                    "unfilled": 0, "closed": 131, "wins": 5, "losses": 126, "open_at_horizon": 28, "data_gap_unresolved": 1,
                    "effective_setup_episodes": 125, "effective_ny_days": 16, "overlap_violations": 0,
                    "gross_pnl_usd": -108.9783, "total_fees_usd": 165.3988, "net_pnl_usd": -274.3771, "expectancy_r": -0.8498,
                    "ny_day_cluster_95ci_r": [-0.9850, -0.7100], "executed_ids_sha256": "c" * 64,
                },
                "KH03_R3_E3_CAUSAL_NEAREST_M5_UNSWEPT_EXIT": {
                    "eligible_candidates_input": 1323, "scheduled": 234, "skipped_due_to_active_position": 1089,
                    "unfilled": 0, "closed": 206, "wins": 9, "losses": 197, "open_at_horizon": 26, "data_gap_unresolved": 2,
                    "effective_setup_episodes": 200, "effective_ny_days": 17, "overlap_violations": 0,
                    "gross_pnl_usd": -158.5045, "total_fees_usd": 280.5378, "net_pnl_usd": -439.0423, "expectancy_r": -0.8628,
                    "ny_day_cluster_95ci_r": [-0.9807, -0.7352], "executed_ids_sha256": "d" * 64,
                },
            }
        }
    }
    return {"rows": rows, "lineage_05": lineage_05, "r2_cands_1x": r2_cands_1x, "dyn6": dyn6}


def test_r4_population_conservation_and_denominator_separation() -> None:
    inp = _make_population_inputs()
    out = build_r4_trade_population_conservation(
        lineage_05=inp["lineage_05"], forensic_rows=inp["rows"], r2_cands_1x=inp["r2_cands_1x"],
        dyn6_scenarios=inp["dyn6"], dataset_sha=CANONICAL_DATASET_SHA256_KH02_R2,
    )
    assert out["status"] == "PASS"
    p = out["primary_baseline_1x_null_conservation"]
    assert p["n_eligible"] == 1323 and p["n_scheduled"] == 233 and p["n_skipped_busy"] == 1090
    assert p["n_closed"] == 203 and p["n_wins"] == 7 and p["n_losses"] == 196
    assert p["n_open_censored"] == 28 and p["n_gap_unresolved"] == 2 and p["n_unfilled"] == 0
    dsep = out["denominator_separation_disclosure"]
    assert dsep["primary_closed_fraction"] == "7/203"
    assert dsep["filled_fraction"] == "7/233"
    assert dsep["primary_closed_win_rate"] == pytest.approx(0.0345, abs=1e-4)
    assert dsep["filled_win_rate_secondary_only"] == pytest.approx(0.0300, abs=1e-4)
    assert dsep["denominators_distinct"] is True


def test_r4_population_conservation_rejects_duplicate_ambiguous_and_overlap() -> None:
    inp = _make_population_inputs()

    # RED 1: Duplicate opportunity_id
    r_dup = copy.deepcopy(inp["rows"])
    r_dup[1]["opportunity_id"] = r_dup[0]["opportunity_id"]
    out_dup = build_r4_trade_population_conservation(
        lineage_05=inp["lineage_05"], forensic_rows=r_dup, r2_cands_1x=inp["r2_cands_1x"],
        dyn6_scenarios=inp["dyn6"], dataset_sha=CANONICAL_DATASET_SHA256_KH02_R2,
    )
    assert out_dup["status"] == "FAIL"
    assert out_dup["conservation_checks"]["zero_duplicate_opportunity_ids"] is False

    # RED 2: Ambiguous outcome label
    r_amb = copy.deepcopy(inp["rows"])
    r_amb[205]["outcome_label"] = "WEIRD_UNCLASSIFIED"
    out_amb = build_r4_trade_population_conservation(
        lineage_05=inp["lineage_05"], forensic_rows=r_amb, r2_cands_1x=inp["r2_cands_1x"],
        dyn6_scenarios=inp["dyn6"], dataset_sha=CANONICAL_DATASET_SHA256_KH02_R2,
    )
    assert out_amb["status"] == "FAIL"
    assert out_amb["conservation_checks"]["zero_ambiguous_outcomes"] is False

    # RED 3: 1-position chronological overlap
    r_ov = copy.deepcopy(inp["rows"])
    r_ov[1]["decision_timestamp_ms"] = r_ov[0]["decision_timestamp_ms"] + 60_000  # while trade 0 holds 15 min
    out_ov = build_r4_trade_population_conservation(
        lineage_05=inp["lineage_05"], forensic_rows=r_ov, r2_cands_1x=inp["r2_cands_1x"],
        dyn6_scenarios=inp["dyn6"], dataset_sha=CANONICAL_DATASET_SHA256_KH02_R2,
    )
    assert out_ov["status"] == "FAIL"
    assert out_ov["conservation_checks"]["zero_1pos_overlap_violations"] is False


def test_r4_censoring_and_e1_e2_e3_non_conflation() -> None:
    inp = _make_population_inputs()
    f06, f09 = audit_r4_covariate_overlap_and_effective_n(
        f15_cost={"dynamic_full_rerisk_six_scenarios": inp["dyn6"]},
        f16_gate={"passing_dev_candidate_arms": [], "primary_arm_evaluations": {}},
        forensic_rows=inp["rows"],
        f10_nc={"status": "PASS", "future_bar_mutation_invariance": {"mismatches_count": 0, "passed": True}, "synthetic_label_and_conservative_tie_controls": {"passed": True}},
        f13_wf={"status": "PASS", "is_fresh_oos": False},
        r2_fold_causality={"valid": True, "status": "PASS", "violations": []},
    )
    assert f06["status"] == "PASS"
    assert f06["baseline_1x_censoring_and_gaps"]["n_open_censored"] == 28
    assert f06["baseline_1x_censoring_and_gaps"]["n_gap_unresolved"] == 2
    assert f06["baseline_1x_censoring_and_gaps"]["open_trades_silently_dropped"] is False
    assert f06["arms_censoring_and_effective_n"]["KH03_R3_E1_LOW_FRICTION_STOP_FLOOR"]["underpowered_flag"] is True

    assert f09["status"] == "PASS"
    assert f09["comparability_classification"] == "INCOMPARABLE_UNMATCHED_POPULATIONS"
    assert f09["raw_pnl_best_arm_selector_prohibited"] is True
    assert f09["all_arms_strictly_negative_ci"] is True
    assert f09["r2_forward_only_dev_embargo_regression"]["zero_future_train_bars"] is True
