"""
aurum-desk/backend/tests/test_kh03_r2_h3_policy_truth.py

KH-03.R2 P0-SCI-02 H3 Policy Truth Unit & Adversarial Tests:
Proves R1 discarded `final_qty`, verifies LONG/SHORT fill-time resize, gap past SL/TP,
min-lot breach, same-bar High/Low future-field rejection, `OPEN_TO_OPEN` instantaneous
causality rejection, PATH B `size_applied_to_execution=False` enforcement, no double fee,
and independent streaming Decimal verification of `08_h3_per_episode_policy_qty_time_ledger.jsonl.gz`.
"""
from __future__ import annotations

import copy
from pathlib import Path
import sys
import pytest

APP_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = APP_ROOT / "backend"
REPO_ROOT = APP_ROOT.parent
for _p in (str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from domain_calculator import CostAssumptions
from services.kh_02_entry_foundation_research_service import build_kh02_asof_mtf_features
from services.kh_02_r2_research_integrity_service import (
    CANONICAL_DATASET_REL_PATH,
    build_r2_causal_unswept_level_ledger,
    build_r2_target_repair_full_opportunity_conservation,
    evaluate_causal_fill_time_policy_single_trade,
    load_r2_canonical_dataset_readonly,
)
from services.kh_03_r2_methodology_service import (
    assert_no_instantaneous_open_to_open_fill,
    build_kh03_r2_h3_advisory_ledger_and_disclosure,
    build_kh03_r2_h3_red_proof_and_policy_choice,
    evaluate_kh03_dev_ci_and_advancement,
    validate_kh03_r2_h3_ledger_independently,
)


def test_h3_synthetic_long_and_short_resize_triggers_final_qty_ne_decision_qty():
    ft_long = evaluate_causal_fill_time_policy_single_trade(
        direction="LONG",
        decision_entry=4125.00,
        fill_bar_open=4125.50,
        selected_sl=4122.00,
        take_profit=4150.00,
        decision_qty=0.45,
        spread_usd=0.20,
        slippage_usd=0.10,
        risk_cap_usd=2.50,
        min_net_rr=2.0,
        now_ms=1791500060000,
    )
    assert ft_long["fill_time_action"] == "RESIZED_CAUSALLY_AT_FILL_TIME"
    assert ft_long["accepted"] is True
    assert ft_long["final_qty"] < ft_long["decision_qty"]
    assert ft_long["final_qty"] >= 0.01
    assert ft_long["fill_time_planned_net_risk_usd"] <= 2.50

    ft_short = evaluate_causal_fill_time_policy_single_trade(
        direction="SHORT",
        decision_entry=4125.00,
        fill_bar_open=4124.50,
        selected_sl=4128.00,
        take_profit=4100.00,
        decision_qty=0.45,
        spread_usd=0.20,
        slippage_usd=0.10,
        risk_cap_usd=2.50,
        min_net_rr=2.0,
        now_ms=1791500060000,
    )
    assert ft_short["fill_time_action"] == "RESIZED_CAUSALLY_AT_FILL_TIME"
    assert ft_short["accepted"] is True
    assert ft_short["final_qty"] < ft_short["decision_qty"]
    assert ft_short["fill_time_planned_net_risk_usd"] <= 2.50


def test_h3_gap_past_sl_tp_min_lot_and_net_rr_rejections():
    g_sl = evaluate_causal_fill_time_policy_single_trade(
        direction="LONG", decision_entry=4125.0, fill_bar_open=4121.0,
        selected_sl=4122.0, take_profit=4150.0, decision_qty=0.30,
    )
    assert g_sl["fill_time_action"] == "REJECTED_FILL_TIME_GAPPED_PAST_SL" and g_sl["accepted"] is False

    g_tp = evaluate_causal_fill_time_policy_single_trade(
        direction="SHORT", decision_entry=4125.0, fill_bar_open=4099.0,
        selected_sl=4128.0, take_profit=4100.0, decision_qty=0.30,
    )
    assert g_tp["fill_time_action"] == "REJECTED_FILL_TIME_GAPPED_PAST_TP" and g_tp["accepted"] is False

    min_lot = evaluate_causal_fill_time_policy_single_trade(
        direction="LONG", decision_entry=4125.0, fill_bar_open=4450.0,
        selected_sl=4100.0, take_profit=5600.0, decision_qty=0.05,
    )
    assert min_lot["accepted"] is False

    low_rr = evaluate_causal_fill_time_policy_single_trade(
        direction="LONG", decision_entry=4125.0, fill_bar_open=4129.0,
        selected_sl=4123.0, take_profit=4132.0, decision_qty=0.20,
    )
    assert low_rr["accepted"] is False


def test_h3_future_high_low_fields_rejected_fail_closed():
    with pytest.raises(ValueError, match="FUTURE_FILL_BAR_HIGH_LOW_FORBIDDEN"):
        evaluate_causal_fill_time_policy_single_trade(
            direction="LONG", decision_entry=4125.0, fill_bar_open=4125.0,
            selected_sl=4122.0, take_profit=4150.0, decision_qty=0.30,
            forbidden_future_fields_present=True,
        )


def test_open_to_open_instantaneous_fill_causality_rejected():
    # Without L1 BBO (M1-only OHLCV): NEVER verifiable
    r1 = assert_no_instantaneous_open_to_open_fill(
        observed_at_ms=1791500060000, assumed_tradeable_at_ms=1791500060000, observed_l1_bbo_available=False
    )
    assert r1["executable_fill_verifiable"] is False

    # Even with L1 flag, identical timestamp (0 latency) is forbidden
    r2 = assert_no_instantaneous_open_to_open_fill(
        observed_at_ms=1791500060000, assumed_tradeable_at_ms=1791500060000, observed_l1_bbo_available=True
    )
    assert r2["executable_fill_verifiable"] is False

    # Only strictly post-observation quote with observed L1 BBO can pass
    r3 = assert_no_instantaneous_open_to_open_fill(
        observed_at_ms=1791500060000, assumed_tradeable_at_ms=1791500060250, observed_l1_bbo_available=True
    )
    assert r3["executable_fill_verifiable"] is True


def test_h3_path_b_disqualified_from_executable_candidate_even_on_positive_synth():
    ev = evaluate_kh03_dev_ci_and_advancement(
        arm_code="KH03_H3_FILL_TIME_CAUSAL_RR_GATE",
        closed_trades_1x=120,
        effective_ny_days_1x=16,
        expectancy_r_1x=0.45,
        net_pnl_usd_1x=135.0,
        expectancy_r_fee_only_0p0006=0.35,
        expectancy_r_2x=0.22,
        raw_ny_day_95ci_r=[0.18, 0.72],
        holm_bonferroni_adjusted_pvalue=0.004,
        matching_quality_status="BALANCED_COMMON_SUPPORT",
        matched_mean_delta_r_vs_null=0.38,
        expectancy_r_vs_placebo_shuffled=0.40,
        expectancy_r_vs_time_offset=0.35,
        wf_median_fold_expectancy_r=0.30,
        wf_positive_fold_ratio=1.0,
        wf_valid_forward_only_folds_count=3,
        executable_edge_eligible=False,
        disqualification_reason="H3_DISQUALIFIED_PATH_B_FILTER_ONLY_NOT_EXECUTABLE_WITH_M1",
    )
    assert ev["passes_preregistered_dev_advancement"] is False
    assert ev["arm_dev_verdict"] == "DISQUALIFIED_FILTER_ONLY_RESEARCH_ADVISORY"
    assert "H3_DISQUALIFIED_PATH_B_FILTER_ONLY_NOT_EXECUTABLE_WITH_M1" in ev["failed_advancement_criteria"]


def test_h3_real_dev_red_proof_and_path_b_advisory_ledger_reconciliation(tmp_path):
    ds, _ = load_r2_canonical_dataset_readonly(REPO_ROOT / CANONICAL_DATASET_REL_PATH)
    # Run on a fast 3,500-bar slice containing real DEV resize events
    ds_sub = {
        "1m": list(ds["1m"])[:3500],
        "5m": [b for b in ds.get("5m", []) if int(b["open_time"]) <= int(ds["1m"][3499]["close_time"])],
        "15m": [b for b in ds.get("15m", []) if int(b["open_time"]) <= int(ds["1m"][3499]["close_time"])],
        "1H": [b for b in ds.get("1H", []) if int(b["open_time"]) <= int(ds["1m"][3499]["close_time"])],
        "4H": [b for b in ds.get("4H", []) if int(b["open_time"]) <= int(ds["1m"][3499]["close_time"])],
        "1D": [b for b in ds.get("1D", []) if int(b["open_time"]) <= int(ds["1m"][3499]["close_time"])],
    }
    mtf = build_kh02_asof_mtf_features(ds_sub)
    led = build_r2_causal_unswept_level_ledger(ds_sub)
    r2c = build_r2_target_repair_full_opportunity_conservation(
        datasets=ds_sub, mtf_bundle=mtf, ledger_bundle=led, r1_candidates=None, cost_assumptions=CostAssumptions()
    )
    f06, f07 = build_kh03_r2_h3_red_proof_and_policy_choice(
        datasets=ds_sub, mtf_bundle=mtf, r2_cands_1x=r2c, tested_code_sha="a" * 40, observed_l1_bbo_available=False
    )
    assert f06["status"] == "PASS"
    assert f06["real_dev_replayed_episodes_where_r1_discarded_final_qty_count"] >= 1
    assert f07["chosen_path"] == "PATH_B_FILTER_ONLY_RESEARCH_ADVISORY"
    assert f07["H3_RESIZE_EXECUTION_STATUS"] == "NOT_VERIFIABLE_WITH_M1"
    assert f07["H3_EXECUTABLE_EDGE_CANDIDATE_ELIGIBLE"] is False

    gz_path = tmp_path / "08_h3_per_episode_policy_qty_time_ledger.jsonl.gz"
    _, f09 = build_kh03_r2_h3_advisory_ledger_and_disclosure(
        datasets=ds_sub, mtf_bundle=mtf, r2_cands_1x=r2c, h3_policy_choice_07=f07, out_h3_ledger_gz_path=gz_path
    )
    assert f09["status"] == "PASS"
    assert f09["size_applied_to_execution_any_row"] is False
    aud = validate_kh03_r2_h3_ledger_independently(gz_path, f09)
    assert aud["valid"] is True and aud["max_residual_usd"] <= 0.001

    # Negative tamper test: zero out fee in summary -> validator catches it
    f09_bad = copy.deepcopy(f09)
    f09_bad["legacy_r1_filter_only_compatibility_summary"]["total_fees_usd"] = 0.0
    aud_bad = validate_kh03_r2_h3_ledger_independently(gz_path, f09_bad)
    assert aud_bad["valid"] is False
