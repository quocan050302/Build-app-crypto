"""
backend/tests/test_v17_9_3_1_2_16_verification_and_forensics.py

Unit test suite for AURUM Desk V17.9.3.1.2.16 verification and forensics service:
  - Dynamically built AuthoritativeMetricsV17931216
  - Real executed negative controls (V101..V140) with real fault injections
  - Audited V01..V100 controls
  - 17 orthogonal gates evaluation
  - Strict validator red/green behavior
  - Cross-view numerical consistency
"""
from __future__ import annotations

import pytest

from services.v17_9_3_1_2_16_verification_and_forensics_service import (
    AuthoritativeMetricsV17931216,
    CANONICAL_DATASET_SHA256_V17931216,
    MANDATORY_BRANCH_V17931216,
    REQUIRED_ARTIFACTS_V17931216,
    VERSION_V17931216,
    build_authoritative_metrics_v17931216,
    evaluate_v17931216_orthogonal_gate_matrix,
    render_v17931216_readme_and_handover,
    run_negative_controls_v01_to_v140_v17931216,
    validate_feature_vs_raw_strict,
    validate_ledger_accounting_strict,
    validate_single_unicorn_confluence_strict,
    validate_sl_tp_sizing_strict,
    validate_system_guards_strict,
    validate_target_candidate_strict,
    verify_v17931216_cross_view_numerical_consistency,
)


def test_v16_constants_and_artifacts():
    assert VERSION_V17931216 == "V17.9.3.1.2.16"
    assert MANDATORY_BRANCH_V17931216 == "feature/v17-9-2-structural-sl-tp-from-qa"
    assert CANONICAL_DATASET_SHA256_V17931216 == "9f301fee2e620c882b205b857fc63b73c3e36f550161f4100e64fcba7ff98c18"
    assert len(REQUIRED_ARTIFACTS_V17931216) >= 28


def test_v16_authoritative_metrics_builder():
    junit_counters = {
        "collected": 5432,
        "executed": 5432,
        "passed": 5430,
        "skipped": 2,
        "failed": 0,
    }
    benchmark_data = {"w3_speedup_vs_w1": 2.391}
    telemetry = {"max_children_observed": 4, "orphan_pids_observed_at_cleanup": []}
    neg_controls = {"total_negative_controls": 140, "passed_negative_controls": 140, "real_injected_count": 40}

    m = build_authoritative_metrics_v17931216(
        junit_counters=junit_counters,
        benchmark_data=benchmark_data,
        resource_telemetry=telemetry,
        neg_controls_res=neg_controls,
    )

    assert m.version == VERSION_V17931216
    assert m.pytest_collected == 5432
    assert m.pytest_executed_junit == 5432
    assert m.pytest_passed == 5430
    assert m.pytest_skipped == 2
    assert m.pytest_failed == 0
    assert m.same_sha_w3_speedup == 2.391
    assert m.negative_controls_real_injected == 40
    assert m.no_core_unicorn_patch_justified is True
    assert m.d4_universe_63085_accepted_nb == 0


def test_v16_real_executed_negative_controls_v101_to_v140():
    res = run_negative_controls_v01_to_v140_v17931216(run_id="TEST_RUN", tested_sha="TEST_SHA")
    assert res["status"] == "PASS"
    assert res["total_negative_controls"] == 140
    assert res["passed_negative_controls"] == 140
    assert res["v101_v140_real_injected_count"] == 40
    assert res["real_injected_count"] >= 40

    v101_v140 = [c for c in res["controls"] if c["category"] == "REAL_EXECUTED_ADVERSARIAL_V101_V140"]
    assert len(v101_v140) == 40
    for c in v101_v140:
        assert c["real_fault_injected"] is True
        assert c["red_fault_rejected"] is True
        assert c["green_control_passed"] is True
        assert c["rejected"] is True
        assert c["mutation_hash"] is not None
        assert c["validator_name"] is not None


def test_v16_strict_validators_red_rejections():
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


def test_v16_orthogonal_gate_matrix_evaluation():
    m = build_authoritative_metrics_v17931216(
        junit_counters={"collected": 5432, "executed": 5432, "passed": 5430, "skipped": 2, "failed": 0},
        benchmark_data={"w3_speedup_vs_w1": 2.391},
        resource_telemetry={"max_children_observed": 4, "orphan_pids_observed_at_cleanup": []},
        neg_controls_res={"total_negative_controls": 140, "passed_negative_controls": 140, "real_injected_count": 40},
    )

    res = evaluate_v17931216_orthogonal_gate_matrix(m, publication_status="BLOCKED_PRE_PUSH_IN_IMMUTABLE_RUN_DIR")
    assert res["status"] == "PASS"

    gates = res["gates"]
    assert len(gates) == 17
    # Mandatory engineering gates must pass
    for g in gates:
        if g["gate_id"] in ("G01", "G02", "G03", "G04", "G05", "G06", "G07", "G08", "G09", "G10", "G11", "G12", "G13", "G16"):
            assert g["status"] == "PASS", f"Gate {g['gate_id']} failed"

    assert next(g for g in gates if g["gate_id"] == "G14")["status"] == "BLOCKED"
    assert next(g for g in gates if g["gate_id"] == "G15")["status"] == "NEGATIVE"
    assert next(g for g in gates if g["gate_id"] == "G17")["status"] == "BLOCKED_PRE_PUSH_IN_IMMUTABLE_RUN_DIR"

    verdicts = res["orthogonal_verdicts"]
    assert verdicts["ENGINEERING_VERDICT"] == "PASS"
    assert verdicts["ACTIVATION_READY"] == "NO"
    assert verdicts["FRESH_OOS_VERDICT"] == "BLOCKED_NO_UNTOUCHED_DATA"
    assert verdicts["STRATEGY_ECONOMIC_EDGE_VERDICT"] == "NEGATIVE_NO_PROVEN_EDGE"


def test_v16_cross_view_numerical_consistency():
    m = build_authoritative_metrics_v17931216(
        junit_counters={"collected": 5432, "executed": 5432, "passed": 5430, "skipped": 2, "failed": 0},
        benchmark_data={"w3_speedup_vs_w1": 2.391},
        resource_telemetry={"max_children_observed": 4, "orphan_pids_observed_at_cleanup": []},
        neg_controls_res={"total_negative_controls": 140, "passed_negative_controls": 140, "real_injected_count": 40},
    )
    gates_doc = evaluate_v17931216_orthogonal_gate_matrix(m)
    readme, handover = render_v17931216_readme_and_handover(m, gates_doc, run_id="TEST_RUN", tested_sha="TEST_SHA")

    cv = verify_v17931216_cross_view_numerical_consistency(m, readme, handover, gates_doc)
    assert cv["status"] == "PASS"
    assert len(cv["violations"]) == 0


def test_v16_red_reproductions():
    from services.v17_9_3_1_2_16_verification_and_forensics_service import execute_red_reproductions_v17931216
    res = execute_red_reproductions_v17931216()
    assert res["status"] == "PASS"
    assert res["total_red_reproductions"] == 10
    assert res["passed_red_reproductions"] == 10

