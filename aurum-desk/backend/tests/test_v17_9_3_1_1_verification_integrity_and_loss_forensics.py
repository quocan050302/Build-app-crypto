from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from services.v17_9_3_1_1_loss_forensics_and_research_service import (
    LOSS_ROOT_CAUSE_TAXONOMY_V179311,
    PREDECLARED_RESEARCH_VARIANT_POLICIES_V179311,
    ResearchVariantPolicyV179311,
    RiskPolicySnapshotV179311,
    V17_9_3_1_1_SCHEMA_VERSION,
    aggregate_v179311_loss_forensics,
    assess_v179311_loss_rate_and_edge_verdict,
    audit_ledger_against_risk_policy_snapshot,
    audit_v179311_evaluator_candidate_coverage,
    build_v179311_loss_episode_forensic_ledger,
    classify_v179311_loss_root_causes,
    compute_contiguous_session_day_block_bootstrap_ci,
    compute_wilson_95_ci,
    evaluate_v179311_chronological_oos_and_walkforward,
    evaluate_v179311_matched_trade_counterfactuals,
    get_canonical_risk_policy_snapshot,
    validate_acceptance_gate_manifest,
    validate_artifact_sha256_manifest,
    validate_junit_and_collection_bijection,
    validate_single_ledger_entry_against_risk_policy,
    validate_trade_temporal_partition_contract,
)
from services.v17_8_backtest_runner import collect_or_load_verified_historical_dataset


REPO_ROOT = Path(__file__).resolve().parents[2]
V17931_LATEST_RUN_JSON = REPO_ROOT / "docs" / "artifacts" / "v17_9_3_1" / "latest_run.json"


@pytest.fixture(scope="module")
def v17931_artifacts_and_datasets() -> Dict[str, Any]:
    assert V17931_LATEST_RUN_JSON.exists(), f"Missing {V17931_LATEST_RUN_JSON}"
    latest = json.loads(V17931_LATEST_RUN_JSON.read_text(encoding="utf-8"))
    run_dir = REPO_ROOT / latest["artifact_dir"]
    assert run_dir.exists(), f"Missing run_dir {run_dir}"
    a04 = json.loads((run_dir / "04_full_22346_decision_universe_and_coverage_proof.json").read_text(encoding="utf-8"))
    a06 = json.loads((run_dir / "06_trade_execution_ledger_net.json").read_text(encoding="utf-8"))
    a07 = json.loads((run_dir / "07_execution_sensitivity_3modes.json").read_text(encoding="utf-8"))
    a11 = json.loads((run_dir / "11_ab_10variant_comparison_dual_scope.json").read_text(encoding="utf-8"))
    a13 = json.loads((run_dir / "13_independent_nonoverlap_episodes.json").read_text(encoding="utf-8"))
    episodes_by_variant = {vk: vdata["episodes"] for vk, vdata in a13["variants"].items()}
    variant_summaries = a11["full_22346_causal_universe"]["variant_summaries"]
    datasets = collect_or_load_verified_historical_dataset(symbol="XAUUSDT", allow_network_refresh=False)
    return {
        "run_dir": run_dir,
        "a03": a04,
        "a05": {
            "episodes_by_variant": episodes_by_variant,
            "variant_summaries": variant_summaries,
        },
        "a06": a06,
        "a07": a07,
        "datasets": datasets,
    }


@pytest.fixture(scope="module")
def precomputed_forensics_and_research(v17931_artifacts_and_datasets: Dict[str, Any]) -> Dict[str, Any]:
    datasets = v17931_artifacts_and_datasets["datasets"]
    a05 = v17931_artifacts_and_datasets["a05"]
    a07 = v17931_artifacts_and_datasets["a07"]
    oos_report = evaluate_v179311_chronological_oos_and_walkforward(
        a05["episodes_by_variant"],
        datasets["1m"],
        embargo_bars=180,
    )
    all_eps: List[Dict[str, Any]] = []
    for vk, eps in a05["episodes_by_variant"].items():
        for ep in eps:
            e_copy = dict(ep)
            e_copy.setdefault("variant", vk)
            all_eps.append(e_copy)
    forensic_rows = build_v179311_loss_episode_forensic_ledger(
        all_eps,
        datasets,
        run_id="test_run_v179311",
        tested_code_sha="test_sha",
        dataset_sha="test_dataset_sha",
        policy_config_hash=get_canonical_risk_policy_snapshot().policy_hash,
    )
    a1_agg = aggregate_v179311_loss_forensics(forensic_rows, variant="A1_V1792_SL_TP_BASELINE")
    d1_agg = aggregate_v179311_loss_forensics(forensic_rows, variant="D1_V1793_MACRO_PLUS_UNICORN_V1792_SL_TP")
    a0_agg = aggregate_v179311_loss_forensics(forensic_rows, variant="A0_FROZEN_BASELINE_OFF")
    counterfactuals = evaluate_v179311_matched_trade_counterfactuals(
        forensic_rows,
        oos_report,
        a07,
    )
    verdict = assess_v179311_loss_rate_and_edge_verdict(
        a05["variant_summaries"],
        a05["episodes_by_variant"],
        oos_report,
        counterfactuals,
    )
    return {
        "oos_report": oos_report,
        "forensic_rows": forensic_rows,
        "a1_agg": a1_agg,
        "d1_agg": d1_agg,
        "a0_agg": a0_agg,
        "counterfactuals": counterfactuals,
        "verdict": verdict,
    }


# ==============================================================================
# T01 - T07: PHASE P0-1 REAL EXIT-TIMESTAMP & 180-BAR EMBARGO OOS CONTRACT TESTS
# ==============================================================================
def test_t01_oos_boundary_crossing_trade_1m_before_dev_end_exiting_30m_into_val_rejected():
    dev_start = 1_000_000 * 60_000
    dev_end = dev_start + 500 * 60_000
    val_start = dev_end + 10 * 60_000  # 10m gap
    leaky_trade = {
        "episode_key": "LEAK_DEV_TO_VAL_01",
        "variant": "A1_V1792_SL_TP_BASELINE",
        "decision_timestamp_ms": dev_end - 60_000,  # 1m before DEV end
        "entry_timestamp_ms": dev_end,
        "exit_timestamp_ms": val_start + 30 * 60_000,  # 30m into VAL
        "filled": True,
        "outcome_label": "LOSS",
        "exit_type": "STOP_LOSS",
        "realized_r": -1.0,
        "realized_net_pnl_usd": -2.50,
    }
    res = validate_trade_temporal_partition_contract(
        [leaky_trade],
        split_start_ts_ms=dev_start,
        split_end_ts_ms=dev_end,
        next_split_start_ts_ms=val_start,
        embargo_minutes=180,
        embargo_bars=180,
    )
    assert res["contract_valid"] is False
    assert res["oos_proof_status"] == "FAIL"
    assert res["boundary_crossing_count"] == 1
    assert res["embargo_leak_count"] == 1
    assert res["per_episode_verdicts"][0]["partition_status"] == "EXCLUDED_BOUNDARY_LEAK_RISK"


def test_t02_oos_180_bar_horizon_trade_crossing_60m_wall_clock_embargo_rejected():
    split_start = 1_000_000 * 60_000
    split_end = split_start + 500 * 60_000
    next_split_start = split_end + 60 * 60_000  # Only 60m embargo (< 180m horizon!)
    trade_180_bars = {
        "episode_key": "HORIZON_180_CROSS_60M_EMBARGO",
        "variant": "E1_OUTSIDE_MACRO_COUNTERFACTUAL",
        "decision_timestamp_ms": split_end - 5 * 60_000,
        "entry_timestamp_ms": split_end - 4 * 60_000,
        "exit_timestamp_ms": split_end + 157 * 60_000,  # Held 157m > 60m embargo
        "bars_held": 157,
        "filled": True,
        "outcome_label": "LOSS",
        "exit_type": "STOP_LOSS",
    }
    res = validate_trade_temporal_partition_contract(
        [trade_180_bars],
        split_start_ts_ms=split_start,
        split_end_ts_ms=split_end,
        next_split_start_ts_ms=next_split_start,
        embargo_minutes=60,
        embargo_bars=180,
    )
    assert res["embargo_sufficient_for_180_bars"] is False
    assert res["contract_valid"] is False
    assert res["embargo_leak_count"] == 1
    assert any("INSUFFICIENT_EMBARGO_WINDOW" in s for s in res["structural_errors"])


def test_t03_oos_provider_gap_aware_180_bar_embargo_detects_cross_gap_trade():
    base_ts = 1_700_000_000_000
    ts_before = [base_ts + i * 60_000 for i in range(200)]
    split_end_ts = ts_before[-1]
    gap_ms = 24 * 60 * 60_000  # 1440m provider gap
    ts_after = [split_end_ts + gap_ms + (i + 1) * 60_000 for i in range(300)]
    m1_ts = ts_before + ts_after
    next_split_start_ts = ts_after[50]
    trade_across_gap = {
        "episode_key": "CROSS_PROVIDER_GAP_TRADE",
        "decision_timestamp_ms": split_end_ts - 60_000,
        "entry_timestamp_ms": split_end_ts,
        "exit_timestamp_ms": ts_after[100],
        "filled": True,
        "outcome_label": "LOSS",
    }
    res = validate_trade_temporal_partition_contract(
        [trade_across_gap],
        split_start_ts_ms=ts_before[0],
        split_end_ts_ms=split_end_ts,
        next_split_start_ts_ms=next_split_start_ts,
        embargo_minutes=180,
        embargo_bars=180,
        m1_timestamps_ms=m1_ts,
    )
    assert res["boundary_has_provider_gap"] is True
    assert res["embargo_sufficient_for_180_bars"] is False
    assert res["embargo_leak_count"] == 1
    assert res["contract_valid"] is False


def test_t04_oos_missing_exit_timestamp_on_filled_trade_fails_contract():
    split_start = 1_700_000_000_000
    split_end = split_start + 300 * 60_000
    next_split = split_end + 200 * 60_000
    missing_exit_trade = {
        "episode_key": "MISSING_EXIT_TS_01",
        "decision_timestamp_ms": split_start + 60_000,
        "entry_timestamp_ms": split_start + 120_000,
        "exit_timestamp_ms": None,
        "exit_fill_ts": None,
        "bars_held": 0,
        "filled": True,
        "outcome_label": "LOSS",
    }
    res = validate_trade_temporal_partition_contract(
        [missing_exit_trade],
        split_start_ts_ms=split_start,
        split_end_ts_ms=split_end,
        next_split_start_ts_ms=next_split,
        embargo_minutes=180,
        embargo_bars=180,
    )
    assert res["unknown_exit_timestamp_count"] == 1
    assert res["contract_valid"] is False
    assert res["per_episode_verdicts"][0]["partition_status"] == "INVALID_UNKNOWN_EXIT_TIMESTAMP"


def test_t05_oos_temporal_ordering_violation_exit_before_entry_rejected():
    split_start = 1_700_000_000_000
    split_end = split_start + 300 * 60_000
    next_split = split_end + 200 * 60_000
    inverted_trade = {
        "episode_key": "INVERTED_EXIT_BEFORE_ENTRY",
        "decision_timestamp_ms": split_start + 120_000,
        "entry_timestamp_ms": split_start + 180_000,
        "exit_timestamp_ms": split_start + 60_000,  # Exit before entry!
        "filled": True,
        "outcome_label": "LOSS",
    }
    res = validate_trade_temporal_partition_contract(
        [inverted_trade],
        split_start_ts_ms=split_start,
        split_end_ts_ms=split_end,
        next_split_start_ts_ms=next_split,
        embargo_minutes=180,
        embargo_bars=180,
    )
    assert res["temporal_order_violation_count"] == 1
    assert res["contract_valid"] is False
    assert any("EXIT_BEFORE_ENTRY" in v for v in res["per_episode_verdicts"][0]["violations"])


def test_t06_real_dataset_chronological_oos_detects_raw_crossings_and_purges_cleanly(
    precomputed_forensics_and_research: Dict[str, Any],
):
    oos_res = precomputed_forensics_and_research["oos_report"]
    assert oos_res["schema_version"] == V17_9_3_1_1_SCHEMA_VERSION
    assert oos_res["raw_unpurged_boundary_crossings_detected_count"] >= 3
    crossing_variants = {r["variant"] for r in oos_res["raw_unpurged_boundary_crossings_detected"]}
    assert "E1_OUTSIDE_MACRO_COUNTERFACTUAL" in crossing_variants
    assert "C2_UNICORN_PLUS_V1792_SL_TP" in crossing_variants
    assert oos_res["measured_admissible_leak_count"] == 0
    assert oos_res["unknown_exit_timestamp_count"] == 0
    assert oos_res["temporal_order_violation_count"] == 0
    assert oos_res["oos_proof_status"] == "OOS_PROOF_PASS"


def test_t07_mutant_unconditional_zero_embargo_leak_is_caught():
    dev_start = 1_700_000_000_000
    dev_end = dev_start + 100 * 60_000
    val_start = dev_end + 60 * 60_000
    leaky = {
        "episode_key": "MUTANT_LEAK_PROBE",
        "decision_timestamp_ms": dev_end - 60_000,
        "entry_timestamp_ms": dev_end,
        "exit_timestamp_ms": val_start + 10 * 60_000,
        "filled": True,
        "outcome_label": "LOSS",
    }
    real_res = validate_trade_temporal_partition_contract(
        [leaky],
        split_start_ts_ms=dev_start,
        split_end_ts_ms=dev_end,
        next_split_start_ts_ms=val_start,
        embargo_minutes=180,
        embargo_bars=180,
    )
    assert real_res["embargo_leak_count"] == 1
    assert real_res["contract_valid"] is False


# ==============================================================================
# T08 - T14: PHASE P0-2 CANONICAL RISK POLICY & LEDGER INVARIANT TESTS
# ==============================================================================
def test_t08_canonical_risk_policy_snapshot_invariants():
    pol = get_canonical_risk_policy_snapshot()
    assert pol.capital_usdt == 1000.0
    assert pol.risk_pct_percent == 0.25
    assert pol.risk_fraction == pytest.approx(0.0025)
    assert pol.max_planned_risk_usd == pytest.approx(2.50)
    assert pol.min_net_rr == pytest.approx(2.0)
    assert pol.quantity_step == pytest.approx(0.01)
    assert pol.min_quantity == pytest.approx(0.01)
    assert pol.multiplier == pytest.approx(1.0)
    assert pol.tick_size == pytest.approx(0.01)
    assert len(pol.policy_hash) == 64


def test_t09_planned_risk_budget_passes_2_49_and_rejects_3_00_10_00_250_00():
    pol = get_canonical_risk_policy_snapshot()
    base_entry = {
        "episode_key": "RISK_TEST_01",
        "variant": "A1_V1792_SL_TP_BASELINE",
        "direction": "SHORT",
        "filled": True,
        "entry_reference": 4500.0,
        "entry_fill_price": 4500.0,
        "exit_fill_price": 4505.0,
        "planned_sl": 4505.0,
        "planned_tp": 4485.0,
        "quantity": 0.45,
        "quantity_step": 0.01,
        "multiplier": 1.0,
        "tick_size": 0.01,
        "gross_notional": 2025.0,
        "entry_fee": 0.81,
        "exit_fee": 0.8109,
        "realized_gross_pnl_usd": -2.25,
        "realized_net_pnl_usd": -3.8709,
        "planned_net_risk_usd": 2.49,
        "realized_r_net": round(-3.8709 / 2.49, 6),
        "outcome_label": "LOSS",
        "fill_mode": "NEXT_BAR_OPEN_BID_ASK",
    }
    pass_res = validate_single_ledger_entry_against_risk_policy(base_entry, pol, decision_net_rr=2.5)
    assert pass_res["valid"] is True, f"Unexpected violations: {pass_res['violations']}"

    for bad_risk in (3.00, 10.00, 250.00):
        bad_entry = dict(base_entry, planned_net_risk_usd=bad_risk, realized_r_net=round(-3.8709 / bad_risk, 6))
        fail_res = validate_single_ledger_entry_against_risk_policy(bad_entry, pol, decision_net_rr=2.5)
        assert fail_res["valid"] is False
        assert any("PLANNED_NET_RISK_OUT_OF_BOUNDS" in v for v in fail_res["violations"])


def test_t10_risk_policy_rejects_unit_mismatch_0_0025_fraction_and_250_cap():
    with pytest.raises(ValueError, match="RISK_PCT_UNIT_MISMATCH"):
        RiskPolicySnapshotV179311(risk_pct_percent=0.0025)
    with pytest.raises(ValueError, match="CANONICAL_RISK_BUDGET_VIOLATION"):
        RiskPolicySnapshotV179311(capital_usdt=100_000.0, risk_pct_percent=0.25)


def test_t11_risk_policy_rejects_min_net_rr_below_2_0_and_stale_1_1():
    with pytest.raises(ValueError, match="CANONICAL_MIN_NET_RR_VIOLATION"):
        RiskPolicySnapshotV179311(min_net_rr=1.1)

    pol = get_canonical_risk_policy_snapshot()
    entry = {
        "episode_key": "LOW_RR_ENTRY",
        "variant": "A1_V1792_SL_TP_BASELINE",
        "direction": "LONG",
        "filled": True,
        "entry_reference": 4500.0,
        "entry_fill_price": 4500.0,
        "exit_fill_price": 4495.0,
        "planned_sl": 4495.0,
        "planned_tp": 4512.0,
        "quantity": 0.30,
        "quantity_step": 0.01,
        "multiplier": 1.0,
        "tick_size": 0.01,
        "gross_notional": 1350.0,
        "entry_fee": 0.54,
        "exit_fee": 0.5394,
        "realized_gross_pnl_usd": -1.50,
        "realized_net_pnl_usd": -2.5794,
        "planned_net_risk_usd": 2.45,
        "realized_r_net": round(-2.5794 / 2.45, 6),
        "outcome_label": "LOSS",
        "fill_mode": "NEXT_BAR_OPEN_BID_ASK",
    }
    res = validate_single_ledger_entry_against_risk_policy(entry, pol, decision_net_rr=1.99)
    assert res["valid"] is False
    assert any("DECISION_NET_RR_BELOW_MIN" in v for v in res["violations"])


def test_t12_realized_gap_loss_exceeding_budget_separated_from_planned_risk_violation():
    pol = get_canonical_risk_policy_snapshot()
    gap_entry = {
        "episode_key": "GAP_SLIPPAGE_LOSS_01",
        "variant": "A1_V1792_SL_TP_BASELINE",
        "direction": "LONG",
        "filled": True,
        "entry_reference": 4500.0,
        "entry_fill_price": 4500.0,
        "exit_fill_price": 4490.0,  # Gap through 4495 SL!
        "planned_sl": 4495.0,
        "planned_tp": 4515.0,
        "quantity": 0.30,
        "quantity_step": 0.01,
        "multiplier": 1.0,
        "tick_size": 0.01,
        "gross_notional": 1350.0,
        "entry_fee": 0.54,
        "exit_fee": 0.5388,
        "realized_gross_pnl_usd": -3.00,
        "realized_net_pnl_usd": -4.0788,
        "planned_net_risk_usd": 2.45,  # Valid planned risk <= 2.50!
        "realized_r_net": round(-4.0788 / 2.45, 6),
        "net_rr": 3.0,
        "outcome_label": "LOSS",
        "fill_mode": "NEXT_BAR_OPEN_BID_ASK",
    }
    aud = audit_ledger_against_risk_policy_snapshot({"A1_V1792_SL_TP_BASELINE": [gap_entry]}, policy=pol)
    assert aud["all_invariants_passed"] is True
    assert aud["planned_risk_budget_violations"] == 0
    assert aud["realized_loss_over_budget_gap_count"] == 1


def test_t13_real_543_ledger_entries_pass_canonical_risk_policy_audit(
    v17931_artifacts_and_datasets: Dict[str, Any],
):
    a06 = v17931_artifacts_and_datasets["a06"]
    a05 = v17931_artifacts_and_datasets["a05"]
    aud = audit_ledger_against_risk_policy_snapshot(
        a06["ledgers_by_variant"],
        episodes_by_variant=a05["episodes_by_variant"],
    )
    assert aud["total_entries_audited"] == 543
    assert aud["all_invariants_passed"] is True
    assert aud["planned_risk_budget_violations"] == 0
    assert aud["stop_geometry_violations"] == 0
    assert aud["quantity_step_violations"] == 0
    assert aud["algebraic_identity_violations"] == 0
    assert aud["min_net_rr_violations"] == 0


def test_t14_v17931_verification_script_stale_250_and_tautological_c25_repaired():
    script_path = REPO_ROOT / "tools" / "run_v17_9_3_1_verification.py"
    src = script_path.read_text(encoding="utf-8")
    assert "p_risk > 250.0" not in src
    assert "max_planned_risk_usd = round(1000.0 * (0.25 / 100.0), 6)" in src
    assert "min_net_rr >= 2.0" in src


# ==============================================================================
# T15: PHASE P0-3 NON-TAUTOLOGICAL GATE MANIFEST VALIDATOR TESTS
# ==============================================================================
def test_t15_non_tautological_gate_validator_rejects_forced_true_self_check_and_schema_defects():
    expected_ids = ["G01", "G02", "G26"]
    valid_payload = {
        "gates": [
            {"gate_id": "G01", "passed": True, "status": "PASS", "description": "Gate 1", "evidence": {"k": 1}},
            {"gate_id": "G02", "passed": True, "status": "PASS", "description": "Gate 2", "evidence": {"k": 2}},
            {"gate_id": "G26", "passed": True, "status": "PASS", "description": "Self check", "evidence": {"k": 3}},
        ]
    }
    res_ok = validate_acceptance_gate_manifest(valid_payload, expected_gate_ids=expected_ids, self_check_gate_id="G26")
    assert res_ok["valid"] is True

    # Mutant: G01 fails, but G26 is hardcoded True -> validator MUST reject as tautological!
    mutant_payload = copy.deepcopy(valid_payload)
    mutant_payload["gates"][0]["passed"] = False
    mutant_payload["gates"][0]["status"] = "FAIL"
    res_mut = validate_acceptance_gate_manifest(mutant_payload, expected_gate_ids=expected_ids, self_check_gate_id="G26")
    assert res_mut["valid"] is False
    assert any("TAUTOLOGICAL_SELF_CHECK_GATE_PASS" in e for e in res_mut["errors"])


# ==============================================================================
# T16 - T17: PHASE P0-4 CANDIDATE COVERAGE AUDITOR TESTS
# ==============================================================================
def test_t16_candidate_coverage_auditor_detects_dropped_eligible_checkpoint(
    v17931_artifacts_and_datasets: Dict[str, Any],
):
    datasets = v17931_artifacts_and_datasets["datasets"]
    a03 = v17931_artifacts_and_datasets["a03"]
    n_slice = 6660
    sliced_datasets = {
        "1m": datasets["1m"][:n_slice],
        "5m": datasets["5m"],
        "15m": datasets["15m"],
        "1H": datasets["1H"],
        "4H": datasets["4H"],
        "1D": datasets["1D"],
    }
    full_screen_except_6652 = [i for i in range(30, n_slice) if i != 6652]
    screen_mock = {
        "screened_candidate_indices": full_screen_except_6652,
        "holdout_verification_indices": [],
        "macro_inside_indices": a03.get("macro_inside_indices", [6652]),
        "bound_unicorn_indices": a03.get("bound_unicorn_indices", [6652]),
    }
    cov = audit_v179311_evaluator_candidate_coverage(
        sliced_datasets,
        screen_mock,
        max_workers=1,
    )
    assert cov["outside_screen_timestamps_audited_by_true_evaluator"] == 1
    assert cov["variant_coverage_matrix"]["A0_FROZEN_BASELINE_OFF"]["outside_screen_false_negative_decisions"] >= 1
    assert cov["variant_coverage_matrix"]["A0_FROZEN_BASELINE_OFF"]["proven_zero_false_negatives_in_3788_screen"] is False
    assert cov["primary_v1793_variants_zero_false_negatives_proven"] is False


def test_t17_candidate_coverage_distinguishes_enumerated_vs_screened_vs_evaluated(
    v17931_artifacts_and_datasets: Dict[str, Any],
):
    datasets = v17931_artifacts_and_datasets["datasets"]
    n_slice = 230
    sliced_datasets = {
        "1m": datasets["1m"][:n_slice],
        "5m": datasets["5m"],
        "15m": datasets["15m"],
        "1H": datasets["1H"],
        "4H": datasets["4H"],
        "1D": datasets["1D"],
    }
    screen_mock = {
        "screened_candidate_indices": list(range(30, 225)),
        "holdout_verification_indices": [225, 226],
        "macro_inside_indices": [],
        "bound_unicorn_indices": [],
    }
    cov = audit_v179311_evaluator_candidate_coverage(sliced_datasets, screen_mock, max_workers=1)
    assert cov["enumerated_decision_timestamps_count"] == 200
    assert cov["enumerated_timestamp_coverage_ratio"] == 1.0
    assert cov["v17931_screen_checkpoint_coverage_ratio"] < 1.0
    assert cov["exhaustive_22346_evaluator_audit_completed"] is True
    assert cov["exhaustive_evaluator_coverage_ratio"] == 1.0


# ==============================================================================
# T18 - T21: PHASE P1-1 LOSS FORENSICS & OUTER STRUCTURE RECONCILIATION TESTS
# ==============================================================================
def test_t18_all_132_a1_and_15_d1_losses_classified_with_zero_unclassified(
    precomputed_forensics_and_research: Dict[str, Any],
):
    forensic_rows = precomputed_forensics_and_research["forensic_rows"]
    a1_losses = [
        r for r in forensic_rows if r["variant"] == "A1_V1792_SL_TP_BASELINE" and r["outcome_label"] == "LOSS"
    ]
    d1_losses = [
        r
        for r in forensic_rows
        if r["variant"] == "D1_V1793_MACRO_PLUS_UNICORN_V1792_SL_TP" and r["outcome_label"] == "LOSS"
    ]
    assert len(a1_losses) == 132
    assert len(d1_losses) == 15
    for rec in a1_losses + d1_losses:
        attr = rec["root_cause_attribution"]
        assert attr is not None
        assert attr["primary_root_cause"] in LOSS_ROOT_CAUSE_TAXONOMY_V179311
        assert attr["primary_root_cause"] != "UNDETERMINABLE_MISSING_CAUSAL_EVIDENCE"
        assert len(attr["contributing_causes"]) >= 1


def test_t19_missing_causal_evidence_returns_undeterminable_fallback():
    stripped_loss = {
        "outcome_label": "LOSS",
        "decision_timestamp_ms": None,
        "entry_fill_price": None,
        "selected_sl": None,
        "causal_pre_entry_features": None,
    }
    cls = classify_v179311_loss_root_causes(stripped_loss)
    assert cls["primary_root_cause"] == "UNDETERMINABLE_MISSING_CAUSAL_EVIDENCE"
    assert cls["confidence"] == "LOW_MISSING_EVIDENCE"


def test_t20_outer_protected_structure_reconciles_6_frozen_a0_episodes(
    precomputed_forensics_and_research: Dict[str, Any],
):
    a0_agg = precomputed_forensics_and_research["a0_agg"]
    assert a0_agg["losses"] == 6
    outer_counts = a0_agg["outer_protected_structure_stop_class_counts"]
    assert outer_counts.get("PREMATURE_NOISE_SWEEP_STOP", 0) == 4
    assert outer_counts.get("DELAYED_INEVITABLE_LOSS", 0) == 1
    assert outer_counts.get("TRUE_INVALIDATION_LOSS", 0) == 1


def test_t21_loss_forensic_aggregation_dimensions_complete(
    precomputed_forensics_and_research: Dict[str, Any],
):
    a1_agg = precomputed_forensics_and_research["a1_agg"]
    assert a1_agg["losses"] == 132
    for key in (
        "primary_root_cause_counts",
        "multilabel_contributing_cause_counts",
        "top_co_occurring_factor_pairs",
        "outer_protected_structure_stop_class_counts",
        "loss_excursion_summary",
        "diagnostic_questions_summary",
        "by_model",
        "by_session",
        "by_macro_window",
        "example_episode_keys_by_cause",
    ):
        assert key in a1_agg and len(a1_agg[key]) > 0


# ==============================================================================
# T22 - T25: PHASE P1-2, P1-3, P1-4 CAUSAL RESEARCH & STATISTICAL VERDICT TESTS
# ==============================================================================
def test_t22_preregistered_research_policies_r0_through_r7_complete():
    assert len(PREDECLARED_RESEARCH_VARIANT_POLICIES_V179311) == 8
    for vid, pol in PREDECLARED_RESEARCH_VARIANT_POLICIES_V179311.items():
        assert isinstance(pol, ResearchVariantPolicyV179311)
        assert pol.enabled_in_production is False
        assert pol.observe_only_outside_backtest is True

    with pytest.raises(ValueError, match="RESEARCH_VARIANT_MUST_REMAIN_OFF"):
        ResearchVariantPolicyV179311(
            variant_id="BAD_LIVE_POLICY",
            base_cohort_variant="A1",
            intervention_category="TEST",
            hypothesis_description="bad",
            cited_dev_loss_cause="TEST",
            enabled_in_production=True,
        )


def test_t23_matched_trade_counterfactuals_subtract_foregone_winners_from_avoided_losers(
    precomputed_forensics_and_research: Dict[str, Any],
):
    cf = precomputed_forensics_and_research["counterfactuals"]
    for grid_key in ("a1_counterfactual_grid", "d1_counterfactual_grid"):
        for vid, comp in cf[grid_key].items():
            expected_net_r = round(comp["avoided_loss_r_gross_benefit"] - comp["foregone_winners_r_cost"], 4)
            assert comp["net_avoided_loss_minus_foregone_win_r"] == pytest.approx(expected_net_r, abs=1e-4)


def test_t24_mandatory_research_questions_q1_through_q10_answered(
    precomputed_forensics_and_research: Dict[str, Any],
):
    cf = precomputed_forensics_and_research["counterfactuals"]
    q_dict = cf["questions_Q1_to_Q10_evidence"]
    for i in range(1, 11):
        matching = [k for k in q_dict if k.startswith(f"Q{i}_")]
        assert len(matching) == 1, f"Missing Q{i} in {list(q_dict.keys())}"
        assert isinstance(q_dict[matching[0]], dict) and len(q_dict[matching[0]]) > 0


def test_t25_statistical_loss_rate_and_edge_verdict_honesty(
    precomputed_forensics_and_research: Dict[str, Any],
):
    verdict = precomputed_forensics_and_research["verdict"]
    assert verdict["ready_for_strategy_activation"] == "NO"
    assert verdict["a1_baseline_verdict"] == "NEGATIVE_EVIDENCE"
    assert verdict["d1_primary_variant_verdict"] == "INCONCLUSIVE"
    assert verdict["d2_mtf_full_variant_verdict"] == "INCONCLUSIVE"
    assert verdict["d2_sample_size_caveat"]["closed_independent_episodes"] == 2
    assert verdict["d1_sample_size_caveat"]["closed_independent_episodes"] == 16
    assert verdict["multiple_testing_disclosure"]["total_hypotheses_tested"] == 18


# ==============================================================================
# T26 - T30: DETERMINISM, JUNIT BIJECTION, SHA256 MANIFEST & SAFETY GATES TESTS
# ==============================================================================
def test_t26_deterministic_forensics_and_counterfactual_reproducibility(
    v17931_artifacts_and_datasets: Dict[str, Any],
):
    datasets = v17931_artifacts_and_datasets["datasets"]
    a05 = v17931_artifacts_and_datasets["a05"]
    d1_eps = [dict(ep, variant="D1_V1793_MACRO_PLUS_UNICORN_V1792_SL_TP") for ep in a05["episodes_by_variant"]["D1_V1793_MACRO_PLUS_UNICORN_V1792_SL_TP"]]
    f1 = build_v179311_loss_episode_forensic_ledger(
        d1_eps,
        datasets,
        run_id="det_run",
        tested_code_sha="sha",
        dataset_sha="dsha",
        policy_config_hash="phash",
    )
    f2 = build_v179311_loss_episode_forensic_ledger(
        d1_eps,
        datasets,
        run_id="det_run",
        tested_code_sha="sha",
        dataset_sha="dsha",
        policy_config_hash="phash",
    )
    h1 = hashlib.sha256(json.dumps(f1, sort_keys=True).encode("utf-8")).hexdigest()
    h2 = hashlib.sha256(json.dumps(f2, sort_keys=True).encode("utf-8")).hexdigest()
    assert h1 == h2


def test_t27_junit_and_collection_bijection_validator(tmp_path: Path):
    xml_ok = tmp_path / "shard_0.xml"
    xml_ok.write_text(
        '<?xml version="1.0" encoding="utf-8"?>'
        '<testsuites><testsuite name="pytest" tests="2" errors="0" failures="0" skipped="0">'
        '<testcase classname="backend.tests.test_sample" name="test_one" time="0.01"/>'
        '<testcase classname="backend.tests.test_sample" name="test_two" time="0.01"/>'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    collected = ["backend/tests/test_sample.py::test_one", "backend/tests/test_sample.py::test_two"]
    res_ok = validate_junit_and_collection_bijection(collected, [xml_ok])
    assert res_ok["bijection_valid"] is True

    res_missing = validate_junit_and_collection_bijection(
        collected + ["backend/tests/test_sample.py::test_three"],
        [xml_ok],
    )
    assert res_missing["bijection_valid"] is False
    assert "backend/tests/test_sample.py::test_three" in res_missing["missing_from_junit"]


def test_t28_artifact_sha256_manifest_validator_detects_corrupt_or_missing_file(tmp_path: Path):
    f1 = tmp_path / "01_test.json"
    content = b'{"ok": true}'
    f1.write_bytes(content)
    good_sha = hashlib.sha256(content).hexdigest()
    res_ok = validate_artifact_sha256_manifest(tmp_path, [{"file": "01_test.json", "sha256": good_sha}])
    assert res_ok["valid"] is True

    res_corrupt = validate_artifact_sha256_manifest(tmp_path, [{"file": "01_test.json", "sha256": "0" * 64}])
    assert res_corrupt["valid"] is False
    assert any("SHA256_MISMATCH" in e for e in res_corrupt["errors"])


def test_t29_runtime_feature_flags_remain_default_off():
    from tools.run_v17_9_3_macro_unicorn_backtest import V1793_VARIANT_POLICIES

    a0_pol = V1793_VARIANT_POLICIES["A0_FROZEN_BASELINE_OFF"]
    assert a0_pol["v17_9_2_structural_sl_enabled"] is False
    assert a0_pol["v17_9_3_macro_unicorn_enabled"] is False
    assert a0_pol["mtf_structure_eligibility_enabled"] is False


def test_t30_28_gate_manifest_contract_self_consistency():
    gate_ids = [f"G{i:02d}" for i in range(1, 29)]
    gates = [
        {
            "gate_id": gid,
            "passed": True,
            "status": "PASS",
            "description": f"Gate {gid} contract",
            "evidence": {"checked": True},
        }
        for gid in gate_ids
    ]
    res = validate_acceptance_gate_manifest({"gates": gates}, expected_gate_ids=gate_ids, self_check_gate_id="G26")
    assert res["valid"] is True
    assert res["total_gates"] == 28
