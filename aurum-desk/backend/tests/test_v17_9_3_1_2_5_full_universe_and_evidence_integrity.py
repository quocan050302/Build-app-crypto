"""
backend/tests/test_v17_9_3_1_2_5_full_universe_and_evidence_integrity.py

AURUM Desk / Bitget XAUUSDT — V17.9.3.1.2.5
Full-Universe Signal Discovery & Evidence Integrity Repair Unit & Regression Tests.
Verifies:
  - P0-A: iter_causal_decision_checkpoints_v1793125 & full-universe candidate discovery
  - P0-B: score_candidate_record_pre_entry_v1793122 no-optimistic-True default & measured ledger
  - P1-C: si8_rescued_invalid_baseline genuine computation & baseline setup classification
  - P1-D: compute_strategy_statistical_verdict_v1793122 full policy coverage & best_research_net_usd
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = BACKEND_ROOT.parent
for p in (str(BACKEND_ROOT), str(APP_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from services.scalp_m1_types import ModelIdentifier
from services.v17_9_3_1_2_2_signal_intelligence import (
    CANONICAL_SIGNAL_INTELLIGENCE_MODES,
    CANONICAL_V1793123_POLICY_IDS,
    compute_strategy_statistical_verdict_v1793122,
    score_candidate_record_pre_entry_v1793122,
)
from services.v17_9_3_1_2_3_core_trading_logic_repair import (
    CANONICAL_5_MODELS_V1793123,
    DEFAULT_TEST_ONLY_SAMPLE_INDICES_V1793123,
    discover_research_candidates_v1793123,
    discover_research_candidates_v1793125,
    iter_causal_decision_checkpoints_v1793125,
)
from tools.run_v17_9_3_1_2_full_universe_backtest import (
    load_canonical_xauusdt_datasets,
)


def test_v1793125_p0_a_iter_causal_decision_checkpoints_census_and_causality() -> None:
    datasets, _ = load_canonical_xauusdt_datasets()
    bars_1m = list(datasets["1m"])
    total_bars = len(bars_1m)
    assert total_bars == 22376

    # Test test-override mode
    override_indices = (5000, 5577, 5600)
    checkpoints = list(
        iter_causal_decision_checkpoints_v1793125(
            datasets, test_only_checkpoint_override=override_indices
        )
    )
    assert len(checkpoints) == 3
    for cp in checkpoints:
        assert cp["checkpoint_index_1m"] in override_indices
        assert cp["data_sufficiency_status"] == "SUFFICIENT"
        assert len(cp["m1_candles"]) <= 181
        assert cp["decision_timestamp_ms"] == int(cp["m1_candles"][-1]["close_time"])

    # Test full universe generator slice (first 50 bars from bar 30)
    first_50 = []
    gen = iter_causal_decision_checkpoints_v1793125(datasets, warmup_bars=30)
    for _ in range(50):
        first_50.append(next(gen))
    assert len(first_50) == 50
    assert first_50[0]["checkpoint_index_1m"] == 30
    assert first_50[-1]["checkpoint_index_1m"] == 79
    # Verify strict ascending chronology
    for i in range(1, len(first_50)):
        assert first_50[i]["decision_timestamp_ms"] > first_50[i - 1]["decision_timestamp_ms"]


def test_v1793125_p0_b_pre_entry_scoring_unproven_features_default_to_false() -> None:
    # Minimal candidate record with missing feature row
    rec_empty_feat = {
        "model_id": "SMC_M1_REVERSAL",
        "direction": "LONG",
        "net_rr": 2.5,
    }
    score_unproven = score_candidate_record_pre_entry_v1793122(rec_empty_feat)
    # Tuple: (htf_align, unicorn_cls, sweep_mss, disp_ok, retest_ok, reach_ok, net_rr, tie_rank)
    # In V17.9.3.1.2.5, missing features MUST NOT default to 1 (True)!
    assert score_unproven[0] == 0  # htf_align != ALIGNED
    assert score_unproven[1] == 0  # unicorn != UNICORN_CONFIRMED
    assert score_unproven[2] == 0  # sweep_ok and mss_ok == False
    assert score_unproven[3] == 0  # disp_ok == False (fixed from default True)
    assert score_unproven[4] == 0  # retest_ok == False
    assert score_unproven[5] == 0  # reach_ok == False (fixed from default True)
    assert score_unproven[6] == 2.5

    # Fully proven candidate record
    rec_proven = {
        "model_id": "SMC_M1_REVERSAL",
        "direction": "LONG",
        "net_rr": 2.5,
    }
    proven_feat = {
        "htf_alignment_state": "ALIGNED",
        "unicorn_classification": "UNICORN_CONFIRMED",
        "sweep_confirmed": True,
        "mss_confirmed": True,
        "displacement_valid": True,
        "fvg_retest_valid": True,
        "target_reachable": True,
    }
    score_proven = score_candidate_record_pre_entry_v1793122(rec_proven, feature_row=proven_feat)
    assert score_proven[0] == 1
    assert score_proven[1] == 1
    assert score_proven[2] == 1
    assert score_proven[3] == 1
    assert score_proven[4] == 1
    assert score_proven[5] == 1
    assert score_proven[6] == 2.5


def test_v1793125_p0_b_discovery_feature_ledger_has_real_measured_fields() -> None:
    datasets, _ = load_canonical_xauusdt_datasets()
    res = discover_research_candidates_v1793123(
        datasets, test_only_checkpoint_override=DEFAULT_TEST_ONLY_SAMPLE_INDICES_V1793123
    )
    ledger = res["si8_discovered_feature_ledger_by_opp_id"]
    assert len(ledger) > 0

    has_false_target = False
    has_mixed_htf = False
    for opp_id, feat in ledger.items():
        assert isinstance(feat["htf_alignment_state"], str)
        assert isinstance(feat["unicorn_classification"], str)
        assert isinstance(feat["displacement_body_atr_ratio"], float)
        assert isinstance(feat["fvg_retest_valid"], bool)
        assert isinstance(feat["fvg_unmitigated"], bool)
        assert isinstance(feat["sweep_confirmed"], bool)
        assert isinstance(feat["sweep_reclaimed"], bool)
        assert isinstance(feat["mss_confirmed"], bool)
        assert isinstance(feat["protected_structure_independent_of_sl"], bool)
        assert isinstance(feat["target_reachable"], bool)
        assert feat["status"] in ("PROVEN_TRUE", "UNPROVEN")

        if not feat["target_reachable"]:
            has_false_target = True
        if feat["htf_alignment_state"] != "ALIGNED":
            has_mixed_htf = True

    # Confirms evidence is genuinely measured, not hardcoded True or ALIGNED
    assert has_false_target is True
    assert has_mixed_htf is True


def test_v1793125_p1_c_non_rescue_and_baseline_setup_classification() -> None:
    datasets, _ = load_canonical_xauusdt_datasets()
    res = discover_research_candidates_v1793123(
        datasets, test_only_checkpoint_override=DEFAULT_TEST_ONLY_SAMPLE_INDICES_V1793123
    )

    assert "si8_rejected_baseline_rescued_count" in res
    assert "si7_rejected_baseline_rescued_count" in res
    assert "si7_upgraded_invalid" in res
    assert "new_si8_valid_when_baseline_no_setup" in res
    assert "new_si8_valid_when_baseline_waiting" in res

    assert res["si7_rejected_baseline_rescued_count"] == 0
    assert res["si7_upgraded_invalid"] == 0
    assert res["si8_rejected_baseline_rescued_count"] == 0


def test_v1793125_p1_d_statistical_verdict_includes_all_policies_and_net_usd() -> None:
    # Build simulated metrics covering SI0..SI8
    mock_metrics = {
        pid: {
            "policy_id": pid,
            "closed_trades_count": 45,
            "expectancy_net_r": -0.7783 if pid == "SI5_STRUCTURAL_ENTRY_SL_AND_REACHABLE_TARGET" else -0.95,
            "total_realized_net_usd": -85.8702 if pid == "SI5_STRUCTURAL_ENTRY_SL_AND_REACHABLE_TARGET" else -120.0,
            "bootstrap_ci_95_low_r": -1.10,
        }
        for pid in CANONICAL_V1793123_POLICY_IDS
    }
    # Add SI8 with specific numbers
    mock_metrics["SI8_RESEARCH_DISCOVERY_REPAIRED"] = {
        "policy_id": "SI8_RESEARCH_DISCOVERY_REPAIRED",
        "closed_trades_count": 10,
        "expectancy_net_r": -0.9983,
        "total_realized_net_usd": -24.7083,
        "bootstrap_ci_95_low_r": -1.25,
    }

    verdict_dict = compute_strategy_statistical_verdict_v1793122(
        policy_metrics_by_id=mock_metrics,
        oos_report={"policies": {}},
        data_quality_ok=True,
        fresh_untouched_holdout_available=False,
    )

    assert verdict_dict["strategy_statistical_verdict"] == "NEGATIVE_EXPECTANCY"
    assert verdict_dict["activation_ready"] == "NO"
    assert verdict_dict["best_research_policy_id"] == "SI5_STRUCTURAL_ENTRY_SL_AND_REACHABLE_TARGET"
    assert verdict_dict["best_research_expectancy_net_r"] == -0.7783
    # P1-D: Must return exact negative realized USD, NOT None or $0.00!
    assert verdict_dict["best_research_net_usd"] == -85.8702
    assert "best_research_net_usd" in verdict_dict

