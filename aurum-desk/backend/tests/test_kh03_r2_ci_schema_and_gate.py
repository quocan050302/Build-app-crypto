"""
aurum-desk/backend/tests/test_kh03_r2_ci_schema_and_gate.py

KH-03.R2 P0-SCI-01 Unit & Adversarial Tests:
Covers >=14 strict CI schema boundaries, R1 Mapping-vs-list RED reproduction,
raw-evidence DEV advancement recomputation, positive/negative/inconclusive/invalid cases,
and exact rejection of false `CROSSES_ZERO` on strictly negative CIs.
"""
from __future__ import annotations

from pathlib import Path
import sys
import pytest

APP_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = APP_ROOT / "backend"
REPO_ROOT = APP_ROOT.parent
for _p in (str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.kh_03_r2_methodology_service import (
    build_kh03_r2_ci_red_green_and_boundary_artifacts,
    evaluate_kh03_dev_ci_and_advancement,
    parse_kh03_cluster_ci_strict,
)


def test_ci_negative_list_classified_valid_negative_not_crosses_zero():
    p = parse_kh03_cluster_ci_strict([-1.0184, -0.8625])
    assert p["available"] is True
    assert p["schema_valid"] is True
    assert p["strictly_positive"] is False
    assert p["status"] == "CI_VALID_NEGATIVE"
    assert p["lower"] == pytest.approx(-1.0184)
    assert p["upper"] == pytest.approx(-0.8625)


def test_ci_positive_list_strictly_positive():
    p = parse_kh03_cluster_ci_strict([0.12, 0.48])
    assert p["available"] is True
    assert p["schema_valid"] is True
    assert p["strictly_positive"] is True
    assert p["status"] == "CI_VALID_POSITIVE"


def test_ci_straddles_zero_classified_correctly():
    p = parse_kh03_cluster_ci_strict([-0.12, 0.04])
    assert p["available"] is True
    assert p["schema_valid"] is True
    assert p["strictly_positive"] is False
    assert p["status"] == "CI_STRADDLES_ZERO"


@pytest.mark.parametrize("bounds", [[0.0, 0.30], [-0.30, 0.0], [0.0, 0.0]])
def test_ci_touches_zero_not_strictly_positive(bounds):
    p = parse_kh03_cluster_ci_strict(bounds)
    assert p["available"] is True
    assert p["schema_valid"] is True
    assert p["strictly_positive"] is False
    assert p["status"] == "CI_TOUCHES_ZERO"


def test_ci_identical_positive_and_negative_bounds():
    p_pos = parse_kh03_cluster_ci_strict([0.15, 0.15])
    assert p_pos["status"] == "CI_VALID_POSITIVE" and p_pos["strictly_positive"] is True
    p_neg = parse_kh03_cluster_ci_strict([-0.5, -0.5])
    assert p_neg["status"] == "CI_VALID_NEGATIVE" and p_neg["strictly_positive"] is False


@pytest.mark.parametrize(
    "bad_val",
    [
        [0.40, 0.10],  # swapped
        [float("nan"), 0.50],  # NaN
        [0.10, float("inf")],  # +Inf
        [float("-inf"), 0.10],  # -Inf
        [True, True],  # bool coercion forbidden
        [False, 0.5],  # bool coercion forbidden
        ["0.10", "0.40"],  # string coercion forbidden
        [],  # empty list
        [0.10],  # length 1
        [0.10, 0.20, 0.30],  # length 3
        {},  # empty mapping
        {"ci_low": 0.1},  # missing ci_high
        {"ci_low": 0.1, "ci_high": 0.3, "extra": 1},  # extra key
        {"foo": 0.1, "bar": 0.3},  # arbitrary mapping
        42.0,  # scalar
    ],
)
def test_ci_invalid_schemas_fail_closed(bad_val):
    p = parse_kh03_cluster_ci_strict(bad_val)
    assert p["schema_valid"] is False
    assert p["available"] is False
    assert p["strictly_positive"] is False
    assert p["status"] == "CI_INVALID_SCHEMA"


def test_ci_none_is_unavailable_inconclusive_not_pass():
    p = parse_kh03_cluster_ci_strict(None)
    assert p["available"] is False
    assert p["schema_valid"] is True
    assert p["strictly_positive"] is False
    assert p["status"] == "CI_UNAVAILABLE_INSUFFICIENT_SAMPLE"


def test_ci_strict_mapping_compat_accepted():
    p = parse_kh03_cluster_ci_strict({"ci_low": 0.08, "ci_high": 0.32})
    assert p["schema_valid"] is True
    assert p["strictly_positive"] is True
    assert p["status"] == "CI_VALID_POSITIVE"


def test_dev_advancement_synthetic_full_positive_passes_only_as_dev_candidate_unproven_oos():
    ev = evaluate_kh03_dev_ci_and_advancement(
        arm_code="SYNTH_POS",
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
    assert ev["passes_preregistered_dev_advancement"] is True
    assert ev["arm_dev_verdict"] == "DEV_CANDIDATE_UNPROVEN_OOS"
    assert ev["failed_advancement_criteria"] == []


def test_dev_advancement_positive_ci_with_other_gates_failing_rejected():
    ev = evaluate_kh03_dev_ci_and_advancement(
        arm_code="SYNTH_POS_CI_ONLY",
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
    assert ev["subgates"]["ci_strictly_positive_ok"] is True
    assert ev["passes_preregistered_dev_advancement"] is False
    assert len(ev["failed_advancement_criteria"]) >= 5


def test_dev_advancement_negative_ci_emits_upper_below_zero_never_crosses_zero():
    ev = evaluate_kh03_dev_ci_and_advancement(
        arm_code="KH03_H1_MTF_UNSWEPT_ALIGN",
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
    assert ev["passes_preregistered_dev_advancement"] is False
    assert ev["ci_parsed"]["status"] == "CI_VALID_NEGATIVE"
    assert any(x.startswith("NY_DAY_CI_UPPER_BELOW_OR_EQUAL_ZERO") for x in ev["failed_advancement_criteria"])
    assert not any("CROSSES_ZERO" in x for x in ev["failed_advancement_criteria"])


def test_real_r1_ci_red_proof_reproduced_from_sealed_r1_artifact():
    r1_15 = (
        REPO_ROOT
        / "aurum-desk/docs/artifacts/kh_03_research_data/20261123T055123Z_f0b668dc0dd1"
        / "15_purged_walkforward_bootstrap_multiplicity_power.json"
    )
    f03, f04, f05 = build_kh03_r2_ci_red_green_and_boundary_artifacts(prior_r1_15_path=r1_15)
    assert f03["status"] == "PASS"
    assert f03["all_3_r1_arms_bug_reproduced"] is True
    assert f03["synthetic_positive_list_false_rejection_proof"]["old_r1_extracted_ci_low"] is None
    assert f04["status"] == "PASS"
    assert f04["passed_boundary_cases"] == f04["total_boundary_cases"] >= 14
    assert f05["status"] == "PASS"
