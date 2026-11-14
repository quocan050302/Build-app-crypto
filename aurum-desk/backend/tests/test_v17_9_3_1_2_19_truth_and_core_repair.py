"""
backend/tests/test_v17_9_3_1_2_19_truth_and_core_repair.py

Unit test suite for AURUM Desk V17.9.3.1.2.19 Truth & Core Repair Service:
  - Strong Typed Evidence Receipts (EvidenceReceiptV17931219)
  - Dynamic AuthoritativeMetricsV17931219 construction and strict zero-fallback validation
  - 280 Real Adversarial Controls (V01..V280) RED/GREEN
  - Mandatory 14-Gate Engineering Acceptance (G01..G13 + G16)
  - Dynamic 17-Gate Matrix evaluation without hardcoded PASS
  - Loss forensics row validator and attribution builder
  - Cross-view numerical consistency checks
  - Red reproductions for Defects D19-01 through D19-20
"""
from __future__ import annotations

from dataclasses import asdict
import pytest

from services.v17_9_3_1_2_19_truth_and_core_repair_service import (
    AuthoritativeMetricsV17931219,
    CANONICAL_DATASET_SHA256_V17931219,
    EvidenceReceiptV17931219,
    MANDATORY_BRANCH_V17931219,
    REQUIRED_ARTIFACTS_V17931219,
    TypedMetricValidationError,
    VERSION_V17931219,
    _validate_loss_forensics_row_strict,
    build_authoritative_metrics_from_evidence_v17931219,
    build_loss_episode_attribution_from_raw_v17931219,
    evaluate_v17931219_orthogonal_gate_matrix,
    execute_red_reproductions_v17931219,
    parse_required_pytest_counts_v17931219,
    render_v17931219_readme_and_handover,
    run_fully_observed_adversarial_suite_v17931219,
    validate_feature_vs_raw_strict,
    validate_ledger_accounting_strict,
    validate_single_unicorn_confluence_strict,
    validate_sl_tp_sizing_strict,
    validate_system_guards_strict,
    validate_target_candidate_strict,
    verify_v17931219_cross_view_numerical_consistency,
)


def test_v19_constants_and_artifacts():
    assert VERSION_V17931219 == "V17.9.3.1.2.19"
    assert MANDATORY_BRANCH_V17931219 == "feature/v17-9-2-structural-sl-tp-from-qa"
    assert CANONICAL_DATASET_SHA256_V17931219 == "9f301fee2e620c882b205b857fc63b73c3e36f550161f4100e64fcba7ff98c18"
    assert len(REQUIRED_ARTIFACTS_V17931219) >= 30
    assert "04_full_negative_control_execution.jsonl.gz" in REQUIRED_ARTIFACTS_V17931219
    assert "06_gate_receipts_from_actual_independent_verifiers.jsonl.gz" in REQUIRED_ARTIFACTS_V17931219
    assert "README_V17_9_3_1_2_19.md" in REQUIRED_ARTIFACTS_V17931219
    assert "HANDOVER_V17_9_3_1_2_19.txt" in REQUIRED_ARTIFACTS_V17931219


def test_v19_evidence_receipt_contract():
    valid = EvidenceReceiptV17931219(
        gate_id="G04",
        status="PASS",
        receipt_path="path/to/receipt.json",
        content_sha256="a" * 64,
        tested_sha="0123456789abcdef0123456789abcdef01234567",
        run_id="UNIT_RUN",
        validator_name="test_validator",
        validator_code_sha="b" * 40,
        input_sha256="c" * 64,
        observed_status="PASS",
        violations=(),
    )
    assert valid.is_valid_pass(expected_run_id="UNIT_RUN", expected_tested_sha="0123456789abcdef0123456789abcdef01234567")
    assert not valid.is_valid_pass(expected_run_id="OTHER_RUN")
    assert not valid.is_valid_pass(expected_tested_sha="ffffffffffffffffffffffffffffffffffffffff")

    # Status not pass
    fail_rcpt = EvidenceReceiptV17931219(
        gate_id="G04",
        status="FAIL",
        receipt_path="path/to/receipt.json",
        content_sha256="a" * 64,
        tested_sha="0123456789abcdef0123456789abcdef01234567",
        run_id="UNIT_RUN",
        validator_name="test_validator",
        validator_code_sha="b" * 40,
        input_sha256="c" * 64,
        observed_status="FAIL",
        violations=("ERR",),
    )
    assert not fail_rcpt.is_valid_pass()


def test_v19_parse_required_pytest_counts():
    valid = {"collected": 5455, "executed": 5455, "passed": 5447, "skipped": 8, "failed": 0, "errors": 0}
    p = parse_required_pytest_counts_v17931219(valid)
    assert p["collected"] == 5455
    assert p["passed"] == 5447
    assert p["skipped"] == 8
    assert p["failed"] == 0
    assert p["errors"] == 0

    # Missing field
    with pytest.raises(TypedMetricValidationError, match="MISSING_REQUIRED_JUNIT_COUNTER:passed"):
        parse_required_pytest_counts_v17931219({"collected": 5455, "executed": 5455, "skipped": 8, "failed": 0})

    # Boolean as integer
    with pytest.raises(TypedMetricValidationError, match="BOOLEAN_AS_INT_REJECTED"):
        parse_required_pytest_counts_v17931219({"collected": True, "executed": 5455, "passed": 5447, "skipped": 8, "failed": 0})

    # Sum mismatch
    with pytest.raises(TypedMetricValidationError, match="JUNIT_EXECUTED_SUM_MISMATCH"):
        parse_required_pytest_counts_v17931219({"collected": 5455, "executed": 5455, "passed": 5430, "skipped": 8, "failed": 0, "errors": 0})


def test_v19_authoritative_metrics_strict_zero_fallbacks():
    junit_counters = {"collected": 5455, "executed": 5455, "passed": 5447, "skipped": 8, "failed": 0, "errors": 0, "collection_exit_code": 0}
    benchmark_data = {"w3_speedup": 2.308}
    telemetry = {"max_children_observed": 3, "orphan_pids_observed_at_cleanup": []}
    neg_controls = {
        "total_negative_controls": 280,
        "passed_negative_controls": 280,
        "actually_executed_count": 280,
        "real_injected_count": 180,
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
    m = build_authoritative_metrics_from_evidence_v17931219(
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
    assert m.version == VERSION_V17931219
    assert m.tested_sha == tested_sha
    assert m.negative_controls_total == 280
    assert m.d4_survivors_14_casebook_count == 14

    # D19-11: Missing scheduled_distribution raises
    bad_loss_census = dict(loss_census)
    del bad_loss_census["scheduled_distribution"]
    with pytest.raises(TypedMetricValidationError, match="MISSING_SCHEDULED_DISTRIBUTION_IN_LOSS_CENSUS"):
        build_authoritative_metrics_from_evidence_v17931219(
            junit_counters=junit_counters,
            benchmark_data=benchmark_data,
            resource_telemetry=telemetry,
            neg_controls_res=neg_controls,
            tested_sha=tested_sha,
            replayed_modes=replayed_modes,
            loss_census=bad_loss_census,
            target_summary=target_summary,
            d4_summary=d4_summary,
        )

    # D19-12: Missing d4 accepted nb raises
    bad_d4 = dict(d4_summary)
    del bad_d4["d4_universe_63085_accepted_nb"]
    with pytest.raises(TypedMetricValidationError, match="MISSING_D4_UNIVERSE_ACCEPTED_NB"):
        build_authoritative_metrics_from_evidence_v17931219(
            junit_counters=junit_counters,
            benchmark_data=benchmark_data,
            resource_telemetry=telemetry,
            neg_controls_res=neg_controls,
            tested_sha=tested_sha,
            replayed_modes=replayed_modes,
            loss_census=loss_census,
            target_summary=target_summary,
            d4_summary=bad_d4,
        )

    # D19-13: Missing actual_net_rr_lt_2_count raises
    bad_tgt = dict(target_summary)
    del bad_tgt["actual_net_rr_lt_2_count"]
    with pytest.raises(TypedMetricValidationError, match="MISSING_ACTUAL_NET_RR_LT_2_COUNT"):
        build_authoritative_metrics_from_evidence_v17931219(
            junit_counters=junit_counters,
            benchmark_data=benchmark_data,
            resource_telemetry=telemetry,
            neg_controls_res=neg_controls,
            tested_sha=tested_sha,
            replayed_modes=replayed_modes,
            loss_census=loss_census,
            target_summary=bad_tgt,
            d4_summary=d4_summary,
        )


def test_v19_dynamic_gate_matrix_evaluation():
    tested_sha = "0123456789abcdef0123456789abcdef01234567"

    # Build valid receipts for all mandatory gates
    receipts = {}
    for gid in ("G03", "G04", "G05", "G07", "G10", "G11", "G12", "G13"):
        receipts[gid] = EvidenceReceiptV17931219(
            gate_id=gid,
            status="PASS",
            receipt_path=f"path/to/{gid}.json",
            content_sha256="a" * 64,
            tested_sha=tested_sha,
            run_id="UNIT_RUN",
            validator_name=f"verifier_{gid}",
            validator_code_sha="b" * 40,
            input_sha256="c" * 64,
            observed_status="PASS",
            violations=(),
        )

    m = AuthoritativeMetricsV17931219(
        version=VERSION_V17931219,
        branch=MANDATORY_BRANCH_V17931219,
        canonical_dataset_sha256=CANONICAL_DATASET_SHA256_V17931219,
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
        pytest_collected=5455,
        pytest_executed_junit=5455,
        pytest_passed=5447,
        pytest_skipped=8,
        pytest_failed=0,
        pytest_errors=0,
        collection_exit_code=0,
        shard_exits_all_zero=True,
        negative_controls_total=280,
        negative_controls_passed=280,
        negative_controls_actually_executed=280,
        negative_controls_real_injected=180,
        negative_controls_report_assertion=100,
        same_sha_w3_speedup=2.308,
        max_children_observed=3,
        orphan_pids_count=0,
        receipts=receipts,
        live_trading_mode="OFF",
        activation_ready="NO",
    )

    eval_res = evaluate_v17931219_orthogonal_gate_matrix(m, publication_status="PRE_PUSH_PENDING")
    assert len(eval_res["gates"]) == 17
    assert eval_res["orthogonal_verdicts"]["ENGINEERING_VERDICT"] == "PASS"
    assert eval_res["orthogonal_verdicts"]["REGRESSION_VERDICT"] == "PASS"
    assert eval_res["orthogonal_verdicts"]["TRUE_UNICORN_CAUSAL_VERDICT"] == "PASS"
    assert eval_res["orthogonal_verdicts"]["D4_MATERIALIZATION_VERDICT"] == "NO_VALID_SETUPS"
    assert eval_res["orthogonal_verdicts"]["FRESH_OOS_VERDICT"] == "BLOCKED_NO_UNTOUCHED_DATA"
    assert eval_res["orthogonal_verdicts"]["STRATEGY_ECONOMIC_EDGE_VERDICT"] == "NEGATIVE_NO_PROVEN_EDGE"
    assert eval_res["orthogonal_verdicts"]["ACTIVATION_READY"] == "NO"

    # D19-10: When a single mandatory gate (e.g. G05) fails, ENGINEERING_VERDICT is FAIL
    bad_receipts = dict(receipts)
    bad_receipts["G05"] = EvidenceReceiptV17931219(
        gate_id="G05",
        status="FAIL",
        receipt_path="path/to/G05.json",
        content_sha256="a" * 64,
        tested_sha=tested_sha,
        run_id="UNIT_RUN",
        validator_name="verifier_G05",
        validator_code_sha="b" * 40,
        input_sha256="c" * 64,
        observed_status="FAIL",
        violations=("PROVENANCE_CORRUPTED",),
    )
    bad_m = AuthoritativeMetricsV17931219(
        **{**asdict(m), "receipts": bad_receipts}
    )
    eval_bad = evaluate_v17931219_orthogonal_gate_matrix(bad_m)
    assert eval_bad["orthogonal_verdicts"]["ENGINEERING_VERDICT"] == "FAIL"


def test_v19_adversarial_suite_execution_v01_v280():
    res = run_fully_observed_adversarial_suite_v17931219(run_id="UNIT_TEST_RUN", tested_sha="0123456789abcdef0123456789abcdef01234567")
    assert res["status"] == "PASS"
    assert res["total_negative_controls"] == 280
    assert res["passed_negative_controls"] == 280
    assert res["actually_executed_count"] == 280
    assert res["real_injected_count"] >= 180
    assert res["report_assertion_count"] <= 100
    assert len(res["controls"]) == 280


def test_v19_red_reproductions_d19_01_through_20():
    repros = execute_red_reproductions_v17931219()
    assert repros["total_reproductions"] >= 20
    assert repros["passed_reproductions"] == repros["total_reproductions"]


def test_v19_loss_forensics_validator():
    # Valid row
    valid_row = {"opportunity_id": "OPP-001", "direction": "SHORT", "decision_timestamp_ms": 1000, "entry_fill_price": 2650.0}
    _validate_loss_forensics_row_strict(valid_row)

    # Missing direction
    with pytest.raises(ValueError, match="MISSING_DIRECTION_IN_LOSS_ROW"):
        _validate_loss_forensics_row_strict({"opportunity_id": "OPP-001", "decision_timestamp_ms": 1000, "entry_fill_price": 2650.0})

    # Non-positive entry price
    with pytest.raises(ValueError, match="INVALID_ENTRY_PRICE_IN_LOSS_ROW"):
        _validate_loss_forensics_row_strict({"opportunity_id": "OPP-001", "direction": "SHORT", "decision_timestamp_ms": 1000, "entry_fill_price": -5.0})

    # Attribution builder
    si0_rows = [valid_row]
    si8_rows = [{"opportunity_id": "SI8-1"}, {"opportunity_id": "SHARED-1"}]
    si9_rows = [{"opportunity_id": "SHARED-1"}, {"opportunity_id": "SI9-1"}]
    attr = build_loss_episode_attribution_from_raw_v17931219(si0_loss_rows=si0_rows, si8_loss_rows=si8_rows, si9_loss_rows=si9_rows)
    assert attr["baseline_si0_losses_count"] == 1
    assert attr["si8_si9_research_losses"]["shared_trade_ids_count"] == 1
    assert attr["si8_si9_research_losses"]["combined_unique_closed_count"] == 3
