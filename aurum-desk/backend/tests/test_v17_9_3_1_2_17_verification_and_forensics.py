"""
backend/tests/test_v17_9_3_1_2_17_verification_and_forensics.py

Comprehensive test suite for AURUM Desk V17.9.3.1.2.17 verification and forensics service:
  - Dynamically built AuthoritativeMetricsV17931217 (purely from evidence, no fallback)
  - Typed metric validation and exception raising on invalid / missing / boolean-as-int values
  - Real executed negative controls (V01..V180) with honest categorization
  - 17 orthogonal gates dynamic evaluation
  - Strict validator red/green behavior
  - Real loss episode attribution with separation of PRE_ENTRY_KNOWN and EX_POST_OUTCOME
  - Cross-view numerical consistency across README, Handover, Gate Matrix, and ledgers
  - Red reproductions R0..R9
"""
from __future__ import annotations

import pytest

from services.v17_9_3_1_2_17_verification_and_forensics_service import (
    AuthoritativeMetricsV17931217,
    CANONICAL_DATASET_SHA256_V17931217,
    MANDATORY_BRANCH_V17931217,
    REQUIRED_ARTIFACTS_V17931217,
    TypedMetricValidationError,
    VERSION_V17931217,
    build_authoritative_metrics_from_evidence_v17931217,
    build_loss_episode_attribution_from_raw_v17931217,
    evaluate_v17931217_orthogonal_gate_matrix,
    execute_red_reproductions_v17931217,
    parse_required_pytest_counts_v17931217,
    render_v17931217_readme_and_handover,
    run_fully_observed_adversarial_suite_v17931217,
    validate_feature_vs_raw_strict,
    validate_ledger_accounting_strict,
    validate_single_unicorn_confluence_strict,
    validate_sl_tp_sizing_strict,
    validate_system_guards_strict,
    validate_target_candidate_strict,
    verify_v17931217_cross_view_numerical_consistency,
)


def test_v17_constants_and_artifacts():
    assert VERSION_V17931217 == "V17.9.3.1.2.17"
    assert MANDATORY_BRANCH_V17931217 == "feature/v17-9-2-structural-sl-tp-from-qa"
    assert CANONICAL_DATASET_SHA256_V17931217 == "9f301fee2e620c882b205b857fc63b73c3e36f550161f4100e64fcba7ff98c18"
    assert len(REQUIRED_ARTIFACTS_V17931217) >= 28


def test_v17_parse_required_pytest_counts_success_and_failures():
    # Valid
    valid = {"collected": 5439, "executed": 5439, "passed": 5437, "skipped": 2, "failed": 0, "errors": 0}
    p = parse_required_pytest_counts_v17931217(valid)
    assert p["collected"] == 5439
    assert p["passed"] == 5437
    assert p["skipped"] == 2
    assert p["failed"] == 0
    assert p["errors"] == 0

    # Missing field
    with pytest.raises(TypedMetricValidationError, match="MISSING_REQUIRED_JUNIT_COUNTER:passed"):
        parse_required_pytest_counts_v17931217({"collected": 5439, "executed": 5439, "skipped": 2, "failed": 0})

    # Boolean as integer
    with pytest.raises(TypedMetricValidationError, match="BOOLEAN_AS_INT_REJECTED"):
        parse_required_pytest_counts_v17931217({"collected": True, "executed": 5439, "passed": 5437, "skipped": 2, "failed": 0})

    # Negative value
    with pytest.raises(TypedMetricValidationError, match="NEGATIVE_JUNIT_COUNTER"):
        parse_required_pytest_counts_v17931217({"collected": 5439, "executed": 5439, "passed": -1, "skipped": 2, "failed": 0})

    # Executed sum mismatch
    with pytest.raises(TypedMetricValidationError, match="JUNIT_EXECUTED_SUM_MISMATCH"):
        parse_required_pytest_counts_v17931217({"collected": 5439, "executed": 5439, "passed": 5430, "skipped": 2, "failed": 0, "errors": 0})

    # Collection bijection failed
    with pytest.raises(TypedMetricValidationError, match="JUNIT_COLLECTION_BIJECTION_FAILED"):
        parse_required_pytest_counts_v17931217({"collected": 5440, "executed": 5439, "passed": 5437, "skipped": 2, "failed": 0, "errors": 0})


def test_v17_authoritative_metrics_builder_strict():
    junit_counters = {"collected": 5439, "executed": 5439, "passed": 5437, "skipped": 2, "failed": 0, "errors": 0}
    benchmark_data = {"w3_speedup_vs_w1": 2.377}
    telemetry = {"max_children_observed": 4, "orphan_pids_observed_at_cleanup": []}
    neg_controls = {
        "total_negative_controls": 180,
        "passed_negative_controls": 180,
        "actually_executed_count": 180,
        "real_injected_count": 80,
        "report_assertion_count": 100,
    }
    replayed_modes = {
        "si0": {
            "NEXT_BAR_OPEN_BID_ASK": {"scheduled_episodes_count": 242, "closed_trades_count": 166, "wins_count": 9, "losses_count": 157, "realized_net_pnl_usd": -345.8971, "expectancy_net_r": -0.8474},
            "LEGACY_CLOSE_FILL": {"scheduled_episodes_count": 242, "closed_trades_count": 166, "wins_count": 9, "losses_count": 157, "realized_net_pnl_usd": -346.7321, "expectancy_net_r": -0.8494},
            "STRESS_WIDER_SPREAD_SLIPPAGE_2X": {"scheduled_episodes_count": 242, "closed_trades_count": 166, "wins_count": 9, "losses_count": 157, "realized_net_pnl_usd": -351.4121, "expectancy_net_r": -0.8609},
        },
        "si8": {
            "NEXT_BAR_OPEN_BID_ASK": {"materialized_intents_count": 5, "blocked_by_open_position_count": 1, "scheduled_episodes_count": 4, "closed_trades_count": 4, "wins_count": 0, "losses_count": 4, "total_fees_usd": 3.0656, "realized_net_pnl_usd": -9.7372, "expectancy_net_r": -0.9876},
            "LEGACY_CLOSE_FILL": {"materialized_intents_count": 5, "blocked_by_open_position_count": 1, "scheduled_episodes_count": 4, "closed_trades_count": 4, "wins_count": 0, "losses_count": 4, "total_fees_usd": 3.0656, "realized_net_pnl_usd": -9.8512, "expectancy_net_r": -0.9992},
            "STRESS_WIDER_SPREAD_SLIPPAGE_2X": {"materialized_intents_count": 5, "blocked_by_open_position_count": 1, "scheduled_episodes_count": 4, "closed_trades_count": 4, "wins_count": 0, "losses_count": 4, "total_fees_usd": 3.1256, "realized_net_pnl_usd": -10.0162, "expectancy_net_r": -1.0159},
        },
        "si9": {
            "NEXT_BAR_OPEN_BID_ASK": {"materialized_intents_count": 3, "blocked_by_open_position_count": 1, "scheduled_episodes_count": 2, "closed_trades_count": 2, "wins_count": 0, "losses_count": 2, "total_fees_usd": 1.3209, "realized_net_pnl_usd": -4.8886, "expectancy_net_r": -0.9896},
            "LEGACY_CLOSE_FILL": {"materialized_intents_count": 3, "blocked_by_open_position_count": 1, "scheduled_episodes_count": 2, "closed_trades_count": 2, "wins_count": 0, "losses_count": 2, "total_fees_usd": 1.3209, "realized_net_pnl_usd": -4.9337, "expectancy_net_r": -0.9987},
            "STRESS_WIDER_SPREAD_SLIPPAGE_2X": {"materialized_intents_count": 3, "blocked_by_open_position_count": 1, "scheduled_episodes_count": 2, "closed_trades_count": 2, "wins_count": 0, "losses_count": 2, "total_fees_usd": 1.3509, "realized_net_pnl_usd": -5.0086, "expectancy_net_r": -1.0139},
        },
    }

    m = build_authoritative_metrics_from_evidence_v17931217(
        junit_counters=junit_counters,
        benchmark_data=benchmark_data,
        resource_telemetry=telemetry,
        neg_controls_res=neg_controls,
        replayed_modes=replayed_modes,
    )

    assert m.version == VERSION_V17931217
    assert m.pytest_collected == 5439
    assert m.pytest_executed_junit == 5439
    assert m.pytest_passed == 5437
    assert m.pytest_skipped == 2
    assert m.pytest_failed == 0
    assert m.same_sha_w3_speedup == 2.377
    assert m.negative_controls_total == 180
    assert m.negative_controls_passed == 180
    assert m.negative_controls_real_injected >= 40
    assert m.no_core_unicorn_patch_justified is True
    assert m.d4_universe_63085_accepted_nb == 0


def test_v17_real_executed_negative_controls_v01_to_v180():
    res = run_fully_observed_adversarial_suite_v17931217(run_id="TEST_RUN", tested_sha="TEST_SHA")
    assert res["status"] == "PASS"
    assert res["total_negative_controls"] == 180
    assert res["passed_negative_controls"] == 180
    assert res["actually_executed_count"] == 180
    assert res["real_injected_count"] >= 80

    for c in res["controls"]:
        assert c["red_fault_rejected"] is True
        assert c["green_control_passed"] is True
        assert c["rejected"] is True
        assert c["mutation_hash"] is not None


def test_v17_strict_validators():
    # 1. Unicorn: missing breaker proof
    ok, msg = validate_single_unicorn_confluence_strict({"breaker_id": None, "fvg_id": "F1"})
    assert not ok
    assert msg == "REJECTED_MISSING_BREAKER_PROOF"

    # 2. Target: M1 ATR ceiling on HTF pool
    ok, msg = validate_target_candidate_strict({
        "pool_id": "P1",
        "raw_price": 2600.0,
        "net_rr": 2.5,
        "source_timeframe": "15M",
        "pool_id_timeframe": "15M",
        "pool_timeframe": "15M",
        "atr_timeframe_applied": "1M",
    })
    assert not ok
    assert msg == "REJECTED_M1_ATR_CEILING_ON_HTF_POOL"

    # 3. SL/TP: Risk > $2.50
    ok, msg = validate_sl_tp_sizing_strict({
        "stop_anchor_price": 2650.0,
        "selected_stop": 2640.0,
        "min_lot_risk_usd": 3.0,
    })
    assert not ok
    assert msg == "REJECTED_RISK_BUDGET_EXCEEDED_2_50_USD"

    # 4. Accounting: double-counted duplicate IDs
    ok, msg = validate_ledger_accounting_strict({
        "duplicate_trade_ids_summed_twice": True,
        "closed_count": 166,
        "wins_count": 9,
        "losses_count": 157,
    })
    assert not ok
    assert msg == "REJECTED_OVERLAPPING_TRADES_DOUBLE_COUNTED"

    # 5. System: activation must be NO
    ok, msg = validate_system_guards_strict({
        "live_trading_mode": "ON",
        "activation_ready": "YES",
    })
    assert not ok
    assert msg == "REJECTED_ACTIVATION_MUST_BE_NO"


def test_v17_orthogonal_gate_matrix_evaluation():
    dummy_m = {
        "branch": MANDATORY_BRANCH_V17931217,
        "canonical_dataset_sha256": CANONICAL_DATASET_SHA256_V17931217,
        "negative_controls_passed": 180,
        "negative_controls_total": 180,
        "negative_controls_real_injected": 80,
        "pytest_collected": 5439,
        "pytest_executed_junit": 5439,
        "pytest_failed": 0,
        "pytest_errors": 0,
        "same_sha_w3_speedup": 2.377,
        "max_children_observed": 4,
        "orphan_pids_count": 0,
        "d4_survivors_14_casebook_count": 14,
        "no_core_unicorn_patch_justified": True,
        "target_267_cohort_count": 267,
        "target_201_m1_vs_htf_atr_mismatch_count": 201,
        "target_66_horizon_unreachable_count": 66,
        "baseline_primary_closed_losses_n157": 157,
        "si8_si9_combined_unique_closed_count": 4,
        "si8_si9_shared_closed_ids_count": 2,
        "d4_universe_63085_accepted_nb": 0,
        "si0_all_modes": {"NEXT_BAR_OPEN_BID_ASK": {"expectancy_net_r": -0.8474}},
        "si8_all_modes": {"NEXT_BAR_OPEN_BID_ASK": {"expectancy_net_r": -0.9876}},
        "si9_all_modes": {"NEXT_BAR_OPEN_BID_ASK": {"expectancy_net_r": -0.9896}},
    }

    res = evaluate_v17931217_orthogonal_gate_matrix(dummy_m, publication_status="PRE_PUSH_PENDING")
    assert res["status"] == "PASS"

    gates = res["gates"]
    assert len(gates) == 17
    # Mandatory engineering gates must pass
    for g in gates:
        if g["gate_id"] in ("G01", "G02", "G03", "G04", "G05", "G06", "G07", "G08", "G09", "G10", "G11", "G12", "G13", "G16"):
            assert g["observed_status"] == "PASS", f"Gate {g['gate_id']} failed: {g['violations']}"

    assert next(g for g in gates if g["gate_id"] == "G14")["observed_status"] == "BLOCKED"
    assert next(g for g in gates if g["gate_id"] == "G15")["observed_status"] == "NEGATIVE"
    assert next(g for g in gates if g["gate_id"] == "G17")["observed_status"] == "PRE_PUSH_PENDING"

    verdicts = res["orthogonal_verdicts"]
    assert verdicts["ENGINEERING_VERDICT"] == "PASS"
    assert verdicts["ACTIVATION_READY"] == "NO"
    assert verdicts["FRESH_OOS_VERDICT"] == "BLOCKED_NO_UNTOUCHED_DATA"
    assert verdicts["STRATEGY_ECONOMIC_EDGE_VERDICT"] == "NEGATIVE_NO_PROVEN_EDGE"


def test_v17_cross_view_numerical_consistency():
    junit_counters = {"collected": 5439, "executed": 5439, "passed": 5437, "skipped": 2, "failed": 0, "errors": 0}
    benchmark_data = {"w3_speedup_vs_w1": 2.377}
    telemetry = {"max_children_observed": 4, "orphan_pids_observed_at_cleanup": []}
    neg_controls = {
        "total_negative_controls": 180,
        "passed_negative_controls": 180,
        "actually_executed_count": 180,
        "real_injected_count": 80,
        "report_assertion_count": 100,
    }
    replayed_modes = {
        "si0": {
            "NEXT_BAR_OPEN_BID_ASK": {"scheduled_episodes_count": 242, "closed_trades_count": 166, "wins_count": 9, "losses_count": 157, "realized_net_pnl_usd": -345.8971, "expectancy_net_r": -0.8474},
            "LEGACY_CLOSE_FILL": {},
            "STRESS_WIDER_SPREAD_SLIPPAGE_2X": {},
        },
        "si8": {
            "NEXT_BAR_OPEN_BID_ASK": {"materialized_intents_count": 5, "blocked_by_open_position_count": 1, "scheduled_episodes_count": 4, "closed_trades_count": 4, "wins_count": 0, "losses_count": 4, "total_fees_usd": 3.0656, "realized_net_pnl_usd": -9.7372, "expectancy_net_r": -0.9876},
            "LEGACY_CLOSE_FILL": {},
            "STRESS_WIDER_SPREAD_SLIPPAGE_2X": {},
        },
        "si9": {
            "NEXT_BAR_OPEN_BID_ASK": {"materialized_intents_count": 3, "blocked_by_open_position_count": 1, "scheduled_episodes_count": 2, "closed_trades_count": 2, "wins_count": 0, "losses_count": 2, "total_fees_usd": 1.3209, "realized_net_pnl_usd": -4.8886, "expectancy_net_r": -0.9896},
            "LEGACY_CLOSE_FILL": {},
            "STRESS_WIDER_SPREAD_SLIPPAGE_2X": {},
        },
    }

    m = build_authoritative_metrics_from_evidence_v17931217(
        junit_counters=junit_counters,
        benchmark_data=benchmark_data,
        resource_telemetry=telemetry,
        neg_controls_res=neg_controls,
        replayed_modes=replayed_modes,
    )
    gates_doc = evaluate_v17931217_orthogonal_gate_matrix(m)
    readme, handover = render_v17931217_readme_and_handover(m, gates_doc, run_id="TEST_RUN", tested_sha="TEST_SHA")

    cv = verify_v17931217_cross_view_numerical_consistency(m, readme, handover, gates_doc)
    assert cv["status"] == "PASS"
    assert len(cv["violations"]) == 0


def test_v17_red_reproductions():
    res = execute_red_reproductions_v17931217()
    assert res["status"] == "PASS"
    assert res["total_red_reproductions"] == 10
    assert res["passed_red_reproductions"] == 10


def test_v17_loss_episode_attribution_real_separation():
    import gzip
    import json
    from tools.run_v17_9_3_1_2_full_universe_backtest import load_canonical_xauusdt_datasets
    from services.v17_9_3_1_2_17_verification_and_forensics_service import V16_RUN_DIR

    raw_path = V16_RUN_DIR / "14_all_30_policy_fill_mode_raw_trade_and_cost_reconciliation.jsonl.gz"
    if not raw_path.exists():
        pytest.skip("V16 raw execution ledger not found")

    raw_rows = []
    with gzip.open(raw_path, "rt", encoding="utf-8") as f:
        for line in f:
            raw_rows.append(json.loads(line))

    datasets, _ = load_canonical_xauusdt_datasets()
    casebook, summary = build_loss_episode_attribution_from_raw_v17931217(raw_rows, datasets)

    assert len(casebook) == 157
    assert summary["total_closed_losses_n157"] == 157
    assert summary["conservation_holds"] is True

    # Check separation
    for c in casebook:
        assert "pre_entry_known" in c
        assert "ex_post_outcome" in c
        # Ensure pre_entry_known does not contain future outcome fields
        assert "exit_fill_price" not in c["pre_entry_known"]
        assert "realized_net_pnl_usd" not in c["pre_entry_known"]
        assert "forensic_class" not in c["pre_entry_known"]

    # Check SI8/SI9 deduplication
    si_res = summary["si8_si9_research_losses"]
    assert si_res["si8_closed_losses_count"] == 4
    assert si_res["si9_closed_losses_count"] == 2
    assert si_res["shared_trade_ids_count"] == 2
    assert si_res["combined_unique_closed_count"] == 4

