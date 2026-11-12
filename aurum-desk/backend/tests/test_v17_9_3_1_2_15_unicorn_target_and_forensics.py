"""
backend/tests/test_v17_9_3_1_2_15_unicorn_target_and_forensics.py
Comprehensive regression and verification test suite for AURUM Desk V17.9.3.1.2.15:
  - Phase 1: 14 D4 survivor root-cause forensics & Breaker+FVG overlap verification
  - Phase 2: 267 target reachability & ATR unit mismatch audit (201/66 conservation)
  - Phase 3: Baseline loss forensics (157 vs 242) and SI8/SI9 4/2 trade deduping
  - Phase 4: D4 14-stage funnel conservation & cross-adapter parity
  - Phase 7: All 140 adversarial negative controls (V01..V140) RED/GREEN
  - Acceptance gates: G01..G17 orthogonal evaluation
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path
import pytest

from services.v17_9_3_1_2_15_unicorn_target_and_forensics_service import (
    AuthoritativeMetricsV17931215,
    CANONICAL_DATASET_SHA256_V17931215,
    MANDATORY_BRANCH_V17931215,
    REQUIRED_ARTIFACTS_V17931215,
    VERSION_V17931215,
    evaluate_v17931215_orthogonal_gate_matrix,
    run_negative_controls_v01_to_v140_v17931215,
    verify_v17931215_cross_view_numerical_consistency,
)

V14_RUN_DIR = Path("docs/artifacts/v17_9_3_1_2_14/20261111T132217Z_0c6403c35522")


def test_authoritative_constants_and_contract():
    assert VERSION_V17931215 == "V17.9.3.1.2.15"
    assert MANDATORY_BRANCH_V17931215 == "feature/v17-9-2-structural-sl-tp-from-qa"
    assert CANONICAL_DATASET_SHA256_V17931215 == "9f301fee2e620c882b205b857fc63b73c3e36f550161f4100e64fcba7ff98c18"
    assert len(REQUIRED_ARTIFACTS_V17931215) >= 28

    m = AuthoritativeMetricsV17931215()
    assert m.canonical_universe_total == 63085
    assert m.baseline_opportunities_total == 56710
    assert m.si8_incremental_discovery_total == 6375
    assert m.d4_survivors_14_casebook_count == 14
    assert m.target_267_cohort_count == 267
    assert m.baseline_primary_closed_losses_n157 == 157
    assert m.baseline_primary_scheduled_episodes_n242 == 242
    assert m.negative_controls_total == 140


def test_d4_14_survivor_casebook_classification():
    f07_path = V14_RUN_DIR / "07_d4_full_unicorn_htf_candidate_funnel_and_rejections.jsonl.gz"
    assert f07_path.exists(), f"Missing {f07_path}"

    survivors_14 = []
    with gzip.open(f07_path, "rt", encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            if row.get("highest_stage_passed") == "04_MSS_BOS_DISPLACEMENT_PROVEN":
                survivors_14.append(row)

    assert len(survivors_14) == 14
    for s in survivors_14:
        assert s.get("model_id") == "CG01_SWEEP_MSS_FVG"
        assert s.get("direction") == "SHORT"
        assert s.get("terminal_rejection") == "05_NOT_TRUE_UNICORN_BREAKER_FVG:FVG_ONLY"
        assert s.get("unicorn_classification") == "FVG_ONLY"


def test_267_target_reconciliation_and_provenance():
    f06_path = V14_RUN_DIR / "06_267_target_failure_root_cause_summary_and_unknown_denominators.json"
    assert f06_path.exists(), f"Missing {f06_path}"

    f06 = json.loads(f06_path.read_text(encoding="utf-8"))
    assert f06.get("historical_267_cohort_count") == 267
    causes = f06.get("mutually_exclusive_primary_root_causes_267") or {}
    assert causes.get("M1_VS_HTF_ATR_UNIT_MISMATCH_ON_VALID_HTF_POOL") == 201
    assert causes.get("GENUINELY_UNREACHABLE_TARGET_BEYOND_TF_HORIZON") == 66
    assert f06.get("prior_v13_bug_diagnosis", {}).get("verified_actual_net_rr_below_2_0_count") == 0
    assert f06.get("control_group_count") >= 40


def test_loss_forensics_157_vs_242_and_si8_si9_deduping():
    f09_path = V14_RUN_DIR / "09_original_execution_ledger_source_reconciliation.jsonl.gz"
    assert f09_path.exists(), f"Missing {f09_path}"

    si0_losses = []
    si8_losses = []
    si9_losses = []
    with gzip.open(f09_path, "rt", encoding="utf-8") as f:
        for line in f:
            e = json.loads(line)
            if e.get("fill_mode") == "NEXT_BAR_OPEN_BID_ASK" and e.get("outcome_label") == "LOSS":
                pol = e.get("policy_id")
                if pol == "SI0_OFF_BASELINE":
                    si0_losses.append(e)
                elif pol == "SI8_RESEARCH_DISCOVERY_REPAIRED":
                    si8_losses.append(e)
                elif pol == "SI9_CAUSAL_SIGNAL_PROVENANCE_AND_EDGE_DIAGNOSIS":
                    si9_losses.append(e)

    assert len(si0_losses) == 157
    assert len(si8_losses) == 4
    assert len(si9_losses) == 2

    # Check shared IDs
    si8_opps = {e["opportunity_id"] for e in si8_losses}
    si9_opps = {e["opportunity_id"] for e in si9_losses}
    shared = si8_opps.intersection(si9_opps)
    assert len(shared) == 2
    # Combined unique trades
    unique_trades = si8_opps.union(si9_opps)
    assert len(unique_trades) == 4


def test_d4_funnel_conservation_and_zero_trade_undefined_expectancy():
    f06_path = V14_RUN_DIR / "06_d4_full_63085_and_matched_242_funnel_by_stage.json"
    if not f06_path.exists():
        # Read from 10 counterfactuals summary
        f10 = json.loads((V14_RUN_DIR / "10_a0_a4_a1b_d1_d4_matched_counterfactuals_3_fill_modes.json").read_text(encoding="utf-8"))
        f_sum = f10.get("d4_full_63085_and_matched_funnel_summary", {})
        stages = f_sum.get("d4_14_stage_pass_counts_63085", {})
    else:
        f06 = json.loads(f06_path.read_text(encoding="utf-8"))
        stages = f06.get("stage_pass_counts") or f06.get("d4_14_stage_pass_counts_63085") or {}

    assert stages.get("01_CANDIDATE_REGISTERED") == 63085
    assert stages.get("02_H4_H1_CAUSAL_STRUCTURAL_ALIGNED") == 7562
    assert stages.get("03_LIQUIDITY_SWEEP_AND_RECLAIM_PROVEN") == 184
    assert stages.get("04_MSS_BOS_DISPLACEMENT_PROVEN") == 14
    assert stages.get("05_TRUE_UNICORN_BREAKER_BOUND_TO_FVG", 0) == 0


def test_negative_controls_v01_to_v140():
    res = run_negative_controls_v01_to_v140_v17931215()
    assert res["status"] == "PASS"
    assert res["total_negative_controls"] == 140
    assert res["passed_negative_controls"] == 140


def test_17_gates_evaluation_and_orthogonal_verdicts():
    m = AuthoritativeMetricsV17931215()
    res = evaluate_v17931215_orthogonal_gate_matrix(m, publication_status="BLOCKED_PRE_PUSH_IN_IMMUTABLE_RUN_DIR")
    assert res["status"] == "PASS"

    gates = res["gates"]
    assert len(gates) == 17
    gate_ids = [g["gate_id"] for g in gates]
    expected_ids = [f"G{i:02d}" for i in range(1, 18)]
    assert gate_ids == expected_ids

    # G14 must be BLOCKED
    assert next(g for g in gates if g["gate_id"] == "G14")["status"] == "BLOCKED"
    # G15 must be NEGATIVE
    assert next(g for g in gates if g["gate_id"] == "G15")["status"] == "NEGATIVE"
    # G16 ACTIVATION must be PASS (meaning ACTIVATION_READY=NO is enforced)
    assert next(g for g in gates if g["gate_id"] == "G16")["status"] == "PASS"
    # G17 PUBLICATION is BLOCKED_PRE_PUSH_IN_IMMUTABLE_RUN_DIR
    assert next(g for g in gates if g["gate_id"] == "G17")["status"] == "BLOCKED_PRE_PUSH_IN_IMMUTABLE_RUN_DIR"

    verdicts = res["orthogonal_verdicts"]
    assert verdicts["ENGINEERING_VERDICT"] == "PASS"
    assert verdicts["TRUE_UNICORN_CAUSAL_VERDICT"] == "PASS"
    assert verdicts["D4_MATERIALIZATION_VERDICT"] == "NO_VALID_SETUPS"
    assert verdicts["TARGET_PROVENANCE_VERDICT"] == "PASS"
    assert verdicts["ACCOUNTING_VERDICT"] == "PASS"
    assert verdicts["REGRESSION_VERDICT"] == "PASS"
    assert verdicts["FRESH_OOS_VERDICT"] == "BLOCKED_NO_UNTOUCHED_DATA"
    assert verdicts["STRATEGY_ECONOMIC_EDGE_VERDICT"] == "NEGATIVE_NO_PROVEN_EDGE"
    assert verdicts["ACTIVATION_READY"] == "NO"
