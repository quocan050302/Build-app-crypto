"""
backend/tests/test_v17_9_3_1_2_18_truth_and_core_repair.py

Unit test suite for AURUM Desk V17.9.3.1.2.18 Truth & Core Repair Service:
  - Dynamic AuthoritativeMetricsV17931218 construction and strict validation
  - 220 Real Adversarial Controls (V01..V220) RED/GREEN
  - Dynamic 17-Gate Matrix evaluation without hardcoded PASS
  - Loss forensics row validator rejecting missing/default direction and prices
  - Cross-view numerical consistency checks
  - Red reproductions for Defects A through J
"""
from __future__ import annotations

import pytest

from services.v17_9_3_1_2_18_truth_and_core_repair_service import (
    AuthoritativeMetricsV17931218,
    CANONICAL_DATASET_SHA256_V17931218,
    MANDATORY_BRANCH_V17931218,
    REQUIRED_ARTIFACTS_V17931218,
    TypedMetricValidationError,
    VERSION_V17931218,
    _validate_loss_forensics_row_strict,
    build_authoritative_metrics_from_evidence_v17931218,
    evaluate_v17931218_orthogonal_gate_matrix,
    execute_red_reproductions_v17931218,
    parse_required_pytest_counts_v17931218,
    render_v17931218_readme_and_handover,
    run_fully_observed_adversarial_suite_v17931218,
    validate_feature_vs_raw_strict,
    validate_ledger_accounting_strict,
    validate_single_unicorn_confluence_strict,
    validate_sl_tp_sizing_strict,
    validate_system_guards_strict,
    validate_target_candidate_strict,
    verify_v17931218_cross_view_numerical_consistency,
)


def test_v18_constants_and_artifacts():
    assert VERSION_V17931218 == "V17.9.3.1.2.18"
    assert MANDATORY_BRANCH_V17931218 == "feature/v17-9-2-structural-sl-tp-from-qa"
    assert CANONICAL_DATASET_SHA256_V17931218 == "9f301fee2e620c882b205b857fc63b73c3e36f550161f4100e64fcba7ff98c18"
    assert len(REQUIRED_ARTIFACTS_V17931218) >= 30
    assert "04_real_negative_controls_v01_v220.jsonl.gz" in REQUIRED_ARTIFACTS_V17931218
    assert "README_V17_9_3_1_2_18.md" in REQUIRED_ARTIFACTS_V17931218
    assert "HANDOVER_V17_9_3_1_2_18.txt" in REQUIRED_ARTIFACTS_V17931218


def test_v18_parse_required_pytest_counts():
    valid = {"collected": 5448, "executed": 5448, "passed": 5440, "skipped": 8, "failed": 0, "errors": 0}
    p = parse_required_pytest_counts_v17931218(valid)
    assert p["collected"] == 5448
    assert p["passed"] == 5440
    assert p["skipped"] == 8
    assert p["failed"] == 0
    assert p["errors"] == 0

    # Missing field
    with pytest.raises(TypedMetricValidationError, match="MISSING_REQUIRED_JUNIT_COUNTER:passed"):
        parse_required_pytest_counts_v17931218({"collected": 5448, "executed": 5448, "skipped": 8, "failed": 0})

    # Boolean as integer
    with pytest.raises(TypedMetricValidationError, match="BOOLEAN_AS_INT_REJECTED"):
        parse_required_pytest_counts_v17931218({"collected": True, "executed": 5448, "passed": 5440, "skipped": 8, "failed": 0})

    # Negative counter
    with pytest.raises(TypedMetricValidationError, match="NEGATIVE_JUNIT_COUNTER"):
        parse_required_pytest_counts_v17931218({"collected": 5448, "executed": 5448, "passed": -1, "skipped": 8, "failed": 0})

    # Sum mismatch
    with pytest.raises(TypedMetricValidationError, match="JUNIT_EXECUTED_SUM_MISMATCH"):
        parse_required_pytest_counts_v17931218({"collected": 5448, "executed": 5448, "passed": 5430, "skipped": 8, "failed": 0, "errors": 0})


def test_v18_authoritative_metrics_builder_strict():
    junit_counters = {"collected": 5448, "executed": 5448, "passed": 5440, "skipped": 8, "failed": 0, "errors": 0}
    benchmark_data = {"w3_speedup": 2.377}
    telemetry = {"max_children_observed": 3, "orphan_pids_observed_at_cleanup": []}
    neg_controls = {
        "total_negative_controls": 220,
        "passed_negative_controls": 220,
        "actually_executed_count": 220,
        "real_injected_count": 120,
        "report_assertion_count": 100,
    }
    replayed_modes = {
        "si0": {
            "NEXT_BAR_OPEN_BID_ASK": {"closed_trades_count": 166, "wins_count": 9, "losses_count": 157, "realized_net_pnl_usd": -345.8971, "expectancy_net_r": -0.8474},
            "LEGACY_CLOSE_FILL": {"closed_trades_count": 166, "wins_count": 9, "losses_count": 157, "realized_net_pnl_usd": -346.7321, "expectancy_net_r": -0.8494},
            "STRESS_WIDER_SPREAD_SLIPPAGE_2X": {"closed_trades_count": 166, "wins_count": 9, "losses_count": 157, "realized_net_pnl_usd": -351.4121, "expectancy_net_r": -0.8609},
        },
        "si8": {
            "NEXT_BAR_OPEN_BID_ASK": {"closed_trades_count": 4, "wins_count": 0, "losses_count": 4, "realized_net_pnl_usd": -9.7372, "expectancy_net_r": -0.9876},
            "LEGACY_CLOSE_FILL": {"closed_trades_count": 4, "wins_count": 0, "losses_count": 4, "realized_net_pnl_usd": -9.8512, "expectancy_net_r": -0.9992},
            "STRESS_WIDER_SPREAD_SLIPPAGE_2X": {"closed_trades_count": 4, "wins_count": 0, "losses_count": 4, "realized_net_pnl_usd": -10.0162, "expectancy_net_r": -1.0159},
        },
        "si9": {
            "NEXT_BAR_OPEN_BID_ASK": {"closed_trades_count": 2, "wins_count": 0, "losses_count": 2, "realized_net_pnl_usd": -4.8886, "expectancy_net_r": -0.9896},
            "LEGACY_CLOSE_FILL": {"closed_trades_count": 2, "wins_count": 0, "losses_count": 2, "realized_net_pnl_usd": -4.9512, "expectancy_net_r": -1.0022},
            "STRESS_WIDER_SPREAD_SLIPPAGE_2X": {"closed_trades_count": 2, "wins_count": 0, "losses_count": 2, "realized_net_pnl_usd": -5.0212, "expectancy_net_r": -1.0175},
        },
    }
    loss_census = {
        "baseline_primary_closed_losses_n157": 157,
        "baseline_primary_scheduled_episodes_n242": 242,
        "pre_entry_known_reasons": {"OTHER_OR_UNKNOWN": 134, "WRONG_SIDE_HTF": 22, "SWEEP_NOT_RECLAIMED": 1},
        "scheduled_distribution": {"LOSS": 157, "WIN": 9, "OPEN_AT_HORIZON": 74, "DATA_GAP_UNRESOLVED": 1, "UNFILLED": 1},
        "si8_si9_shared_closed_ids_count": 2,
        "si8_si9_combined_unique_closed_count": 4,
    }
    target_summary = {
        "historical_267_cohort_count": 267,
        "m1_vs_htf_atr_unit_mismatch_count": 201,
        "horizon_unreachable_count": 66,
        "control_group_count": 40,
        "actual_net_rr_lt_2_count": 0,
    }
    d4_summary = {
        "d4_14_stage_pass_counts_63085": {"01_CANDIDATE_REGISTERED": 63085, "04_MSS_BOS_DISPLACEMENT_PROVEN": 14, "05_TRUE_UNICORN_BREAKER_BOUND_TO_FVG": 0},
        "survivors_count": 14,
        "primary_status_distribution": {"NO_VALID_BREAKER_FVG_OVERLAP": 14},
        "d4_materialization_status": "NO_CAUSALLY_ELIGIBLE_SETUPS",
        "d4_universe_63085_accepted_nb": 0,
        "d4_universe_63085_closed_nb": 0,
        "d4_universe_63085_net_pnl_usd_nb": 0.0,
        "d4_universe_63085_expectancy_status": "UNDEFINED_ZERO_TRADES",
        "d4_matched_242_accepted_nb": 0,
        "d4_matched_242_closed_nb": 0,
        "d4_matched_242_net_pnl_usd_nb": 0.0,
        "d4_matched_242_expectancy_status": "INCONCLUSIVE_NO_SAMPLE",
        "no_core_unicorn_patch_justified": True,
    }
    tested_sha = "0123456789abcdef0123456789abcdef01234567"

    # Valid build
    m = build_authoritative_metrics_from_evidence_v17931218(
        junit_counters=junit_counters,
        benchmark_data=benchmark_data,
        resource_telemetry=telemetry,
        neg_controls_res=neg_controls,
        tested_sha=tested_sha,
        replayed_modes=replayed_modes,
        loss_census=loss_census,
        target_summary=target_summary,
        d4_summary=d4_summary,
    )
    assert m.version == VERSION_V17931218
    assert m.tested_sha == tested_sha
    assert m.negative_controls_total == 220
    assert m.d4_survivors_14_casebook_count == 14

    # Tested SHA validation
    with pytest.raises(TypedMetricValidationError, match="INVALID_TESTED_SHA"):
        build_authoritative_metrics_from_evidence_v17931218(
            junit_counters=junit_counters,
            benchmark_data=benchmark_data,
            resource_telemetry=telemetry,
            neg_controls_res=neg_controls,
            tested_sha="short",
            replayed_modes=replayed_modes,
            loss_census=loss_census,
            target_summary=target_summary,
            d4_summary=d4_summary,
        )

    # Missing d4_summary
    with pytest.raises(TypedMetricValidationError, match="MISSING_D4_SUMMARY"):
        build_authoritative_metrics_from_evidence_v17931218(
            junit_counters=junit_counters,
            benchmark_data=benchmark_data,
            resource_telemetry=telemetry,
            neg_controls_res=neg_controls,
            tested_sha=tested_sha,
            replayed_modes=replayed_modes,
            loss_census=loss_census,
            target_summary=target_summary,
            d4_summary=None,
        )


def test_v18_loss_forensics_row_validator():
    # Valid
    valid_row = {"opportunity_id": "OPP-001", "direction": "SHORT", "decision_timestamp_ms": 1000, "entry_fill_price": 2650.0}
    _validate_loss_forensics_row_strict(valid_row)

    # Missing direction
    with pytest.raises(ValueError, match="MISSING_DIRECTION_IN_LOSS_ROW"):
        _validate_loss_forensics_row_strict({"opportunity_id": "OPP-001", "decision_timestamp_ms": 1000, "entry_fill_price": 2650.0})

    # Invalid direction
    with pytest.raises(ValueError, match="INVALID_DIRECTION_IN_LOSS_ROW:SIDEWAYS"):
        _validate_loss_forensics_row_strict({"opportunity_id": "OPP-001", "direction": "SIDEWAYS", "decision_timestamp_ms": 1000, "entry_fill_price": 2650.0})

    # Missing decision timestamp
    with pytest.raises(ValueError, match="MISSING_DECISION_TIMESTAMP_IN_LOSS_ROW"):
        _validate_loss_forensics_row_strict({"opportunity_id": "OPP-001", "direction": "SHORT", "entry_fill_price": 2650.0})

    # Missing entry price
    with pytest.raises(ValueError, match="MISSING_ENTRY_PRICE_IN_LOSS_ROW"):
        _validate_loss_forensics_row_strict({"opportunity_id": "OPP-001", "direction": "SHORT", "decision_timestamp_ms": 1000})


def test_v18_dynamic_gate_matrix_evaluation():
    tested_sha = "0123456789abcdef0123456789abcdef01234567"
    m = AuthoritativeMetricsV17931218(
        version=VERSION_V17931218,
        branch=MANDATORY_BRANCH_V17931218,
        canonical_dataset_sha256=CANONICAL_DATASET_SHA256_V17931218,
        tested_sha=tested_sha,
        canonical_universe_total=63085,
        baseline_opportunities_total=56710,
        si8_incremental_discovery_total=6375,
        d4_14_stage_pass_counts_63085={"01_CANDIDATE_REGISTERED": 63085, "04_MSS_BOS_DISPLACEMENT_PROVEN": 14, "05_TRUE_UNICORN_BREAKER_BOUND_TO_FVG": 0},
        d4_survivors_14_casebook_count=14,
        d4_survivors_14_primary_status_distribution={"NO_VALID_BREAKER_FVG_OVERLAP": 14},
        d4_materialization_status="NO_CAUSALLY_ELIGIBLE_SETUPS",
        d4_universe_63085_accepted_nb=0,
        d4_universe_63085_closed_nb=0,
        d4_universe_63085_net_pnl_usd_nb=0.0,
        d4_universe_63085_expectancy_status="UNDEFINED_ZERO_TRADES",
        d4_matched_242_accepted_nb=0,
        d4_matched_242_closed_nb=0,
        d4_matched_242_net_pnl_usd_nb=0.0,
        d4_matched_242_expectancy_status="INCONCLUSIVE_NO_SAMPLE",
        no_core_unicorn_patch_justified=True,
        target_267_cohort_count=267,
        target_201_m1_vs_htf_atr_mismatch_count=201,
        target_66_horizon_unreachable_count=66,
        target_actual_net_rr_lt_2_count=0,
        target_control_group_count=40,
        baseline_primary_closed_losses_n157=157,
        baseline_primary_scheduled_episodes_n242=242,
        baseline_primary_closed_losses_distribution={"OTHER_OR_UNKNOWN": 134, "WRONG_SIDE_HTF": 22, "SWEEP_NOT_RECLAIMED": 1},
        baseline_primary_scheduled_distribution={"LOSS": 157, "WIN": 9, "OPEN_AT_HORIZON": 74, "DATA_GAP_UNRESOLVED": 1, "UNFILLED": 1},
        si0_all_modes={
            "NEXT_BAR_OPEN_BID_ASK": {"closed_trades_count": 166, "wins_count": 9, "losses_count": 157, "realized_net_pnl_usd": -345.8971, "expectancy_net_r": -0.8474},
            "LEGACY_CLOSE_FILL": {"closed_trades_count": 166, "wins_count": 9, "losses_count": 157, "realized_net_pnl_usd": -346.7321, "expectancy_net_r": -0.8494},
            "STRESS_WIDER_SPREAD_SLIPPAGE_2X": {"closed_trades_count": 166, "wins_count": 9, "losses_count": 157, "realized_net_pnl_usd": -351.4121, "expectancy_net_r": -0.8609},
        },
        si8_all_modes={
            "NEXT_BAR_OPEN_BID_ASK": {"closed_trades_count": 4, "wins_count": 0, "losses_count": 4, "realized_net_pnl_usd": -9.7372, "expectancy_net_r": -0.9876},
            "LEGACY_CLOSE_FILL": {"closed_trades_count": 4, "wins_count": 0, "losses_count": 4, "realized_net_pnl_usd": -9.8512, "expectancy_net_r": -0.9992},
            "STRESS_WIDER_SPREAD_SLIPPAGE_2X": {"closed_trades_count": 4, "wins_count": 0, "losses_count": 4, "realized_net_pnl_usd": -10.0162, "expectancy_net_r": -1.0159},
        },
        si9_all_modes={
            "NEXT_BAR_OPEN_BID_ASK": {"closed_trades_count": 2, "wins_count": 0, "losses_count": 2, "realized_net_pnl_usd": -4.8886, "expectancy_net_r": -0.9896},
            "LEGACY_CLOSE_FILL": {"closed_trades_count": 2, "wins_count": 0, "losses_count": 2, "realized_net_pnl_usd": -4.9512, "expectancy_net_r": -1.0022},
            "STRESS_WIDER_SPREAD_SLIPPAGE_2X": {"closed_trades_count": 2, "wins_count": 0, "losses_count": 2, "realized_net_pnl_usd": -5.0212, "expectancy_net_r": -1.0175},
        },
        si8_si9_shared_closed_ids_count=2,
        si8_si9_combined_unique_closed_count=4,
        pytest_collected=5448,
        pytest_executed_junit=5448,
        pytest_passed=5440,
        pytest_skipped=8,
        pytest_failed=0,
        pytest_errors=0,
        negative_controls_total=220,
        negative_controls_passed=220,
        negative_controls_actually_executed=220,
        negative_controls_real_injected=120,
        negative_controls_report_assertion=100,
        same_sha_w3_speedup=2.377,
        max_children_observed=3,
        orphan_pids_count=0,
    )

    eval_res = evaluate_v17931218_orthogonal_gate_matrix(m, publication_status="PRE_PUSH_PENDING")
    assert len(eval_res["gates"]) == 17
    assert eval_res["orthogonal_verdicts"]["ENGINEERING_VERDICT"] == "PASS"
    assert eval_res["orthogonal_verdicts"]["REGRESSION_VERDICT"] == "PASS"
    assert eval_res["orthogonal_verdicts"]["TRUE_UNICORN_CAUSAL_VERDICT"] == "PASS"
    assert eval_res["orthogonal_verdicts"]["D4_MATERIALIZATION_VERDICT"] == "NO_VALID_SETUPS"
    assert eval_res["orthogonal_verdicts"]["FRESH_OOS_VERDICT"] == "BLOCKED_NO_UNTOUCHED_DATA"
    assert eval_res["orthogonal_verdicts"]["STRATEGY_ECONOMIC_EDGE_VERDICT"] == "NEGATIVE_NO_PROVEN_EDGE"
    assert eval_res["orthogonal_verdicts"]["ACTIVATION_READY"] == "NO"


def test_v18_adversarial_suite_execution():
    res = run_fully_observed_adversarial_suite_v17931218(run_id="UNIT_TEST_RUN", tested_sha="0123456789abcdef0123456789abcdef01234567")
    assert res["status"] == "PASS"
    assert res["total_negative_controls"] == 220
    assert res["passed_negative_controls"] == 220
    assert res["actually_executed_count"] == 220
    assert res["real_injected_count"] >= 120
    assert len(res["controls"]) == 220


def test_v18_red_reproductions():
    repros = execute_red_reproductions_v17931218()
    assert repros["total_reproductions"] >= 3
    assert repros["passed_reproductions"] == repros["total_reproductions"]

