"""
backend/tests/test_v17_9_3_1_2_4_si8_discovery_and_fresh_regression.py

AURUM Desk / Bitget XAUUSDT — V17.9.3.1.2.4
SI8 Research Discovery Repair, Fresh Regression Truth & Real Strategy Validation Tests.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any, Dict
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = BACKEND_ROOT.parent
for p in (str(BACKEND_ROOT), str(APP_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from services.scalp_m1_types import M1ScalpConfig, ModelIdentifier
from services.v17_9_3_1_2_2_signal_intelligence import (
    CANONICAL_SIGNAL_INTELLIGENCE_MODES,
    CANONICAL_V1793123_POLICY_IDS,
    PREDECLARED_SIGNAL_INTELLIGENCE_POLICIES_V1793122,
    is_research_apply_forbidden_in_adapter_v1793122,
    normalize_signal_intelligence_mode_v1793122,
)
from services.v17_9_3_1_2_3_core_trading_logic_repair import (
    CANONICAL_5_MODELS_V1793123,
    classify_single_episode_root_causes_v1793123,
    discover_research_candidates_v1793123,
)
from tools.run_v17_9_3_1_2_2_verification import (
    verify_fresh_junit_shards_attestation_v1793124,
)
from tools.run_v17_9_3_1_2_full_universe_backtest import (
    load_canonical_xauusdt_datasets,
)


def test_v1793124_r1_si8_mode_contract_and_invalid_mode_fail_closed() -> None:
    assert CANONICAL_SIGNAL_INTELLIGENCE_MODES == ("OFF", "OBSERVE_ONLY", "RESEARCH_APPLY")
    mode_norm, is_valid, err = normalize_signal_intelligence_mode_v1793122("RESEARCH_DISCOVERY")
    assert mode_norm == "INVALID_SIGNAL_INTELLIGENCE_MODE"
    assert is_valid is False
    assert err == "SI_INVALID_MODE_FAIL_CLOSED"

    si8_pol = PREDECLARED_SIGNAL_INTELLIGENCE_POLICIES_V1793122["SI8_RESEARCH_DISCOVERY_REPAIRED"]
    assert si8_pol.enable_core_trading_logic_repair_v1793123 is True
    assert si8_pol.enable_research_discovery_v1793123 is True

    cfg_ok = M1ScalpConfig(
        execution_mode="SHADOW",
        v17_9_3_1_2_2_signal_intelligence_mode="RESEARCH_APPLY",
        v17_9_3_1_2_2_policy_id="SI8_RESEARCH_DISCOVERY_REPAIRED",
    )
    assert cfg_ok.v17_9_3_1_2_2_signal_intelligence_mode == "RESEARCH_APPLY"
    assert cfg_ok.v17_9_3_1_2_2_policy_id == "SI8_RESEARCH_DISCOVERY_REPAIRED"

    for prod_mode in ("PAPER", "LIVE", "AUTO_PAPER", "AUTO_LIVE"):
        with pytest.raises(ValueError, match="FORBIDDEN_IN_LIVE_OR_PAPER_EXECUTION_MODE"):
            M1ScalpConfig(
                execution_mode=prod_mode,
                v17_9_3_1_2_2_signal_intelligence_mode="RESEARCH_APPLY",
                v17_9_3_1_2_2_policy_id="SI8_RESEARCH_DISCOVERY_REPAIRED",
            )
    forb, reason = is_research_apply_forbidden_in_adapter_v1793122("PRODUCTION_ORCHESTRATOR")
    assert forb is True and reason == "SI_FORBIDDEN_IN_PRODUCTION_ADAPTER"


def test_v1793124_r2_discover_research_candidates_all_5_models_incremental_and_non_rescue() -> None:
    datasets, _ = load_canonical_xauusdt_datasets()
    res = discover_research_candidates_v1793123(datasets)

    assert res["si8_canonical_mode"] == "RESEARCH_APPLY"
    assert res["si8_canonical_policy_id"] == "SI8_RESEARCH_DISCOVERY_REPAIRED"
    assert res["legacy_mode_mismatch_reproduction"]["fail_closed_verified"] is True
    assert "SI_INVALID_MODE_FAIL_CLOSED" in res["legacy_mode_mismatch_reproduction"]["rejection_reasons"]

    assert res["si7_rejected_baseline_rescued_count"] == 0
    assert res["si8_rejected_baseline_rescued_count"] == 0
    assert res["si8_newly_discovered_setups_count"] > 0
    assert res["si8_incremental_eligible_vs_si7_count"] > 0

    funnel = res["funnel_by_model"]
    for m_id in CANONICAL_5_MODELS_V1793123:
        assert m_id in funnel
        assert funnel[m_id]["observed"] == 40
        assert funnel[m_id]["descriptor_found"] > 0
        assert funnel[m_id]["setup_valid"] > 0
        assert funnel[m_id]["eligible_after_risk"] > 0
        assert funnel[m_id]["incremental_vs_si7"] > 0

    cg01_diag = res["cg01_starvation_forensics"]
    assert cg01_diag["model_id"] == ModelIdentifier.CG01_SWEEP_MSS_FVG.value
    assert cg01_diag["cg01_funnel"]["eligible_after_risk"] > 0


def test_v1793124_r3_unproven_root_cause_falls_back_to_other_unknown() -> None:
    ep_minimal: Dict[str, Any] = {
        "opportunity_id": "opp_minimal_1",
        "model_id": "SMC_M1_REVERSAL",
        "outcome_label": "LOSS",
    }
    primary, labels = classify_single_episode_root_causes_v1793123(ep_minimal, {})
    assert primary == "OTHER/UNKNOWN"
    assert labels == ["OTHER/UNKNOWN"]


def test_v1793124_r4_fresh_junit_attestation_and_stale_negative_control() -> None:
    mtime_ns = 1_793_000_000_000_000_000
    valid_shard = {
        "shard_name": "shard_00",
        "reused": False,
        "tested_executable_sha": "a" * 40,
        "pid": 12345,
        "pytest_exit_code": 0,
        "failed_count": 0,
        "error_count": 0,
        "exact_bijection": True,
        "xml_mtime_ns": mtime_ns,
        "xml_sha256": "deadbeef" * 8,
    }
    valid_report = {
        "tested_executable_sha": "a" * 40,
        "run_start_epoch_ns": mtime_ns - 100_000_000,
        "reused_shards_count": 0,
        "shards": [valid_shard],
    }
    att_ok = verify_fresh_junit_shards_attestation_v1793124(
        valid_report,
        expected_tested_executable_sha="a" * 40,
        min_mtime_ns=mtime_ns - 100_000_000,
    )
    assert att_ok["passed"] is True

    # Negative control 1: Reused shard from prior run must fail RED
    reused_report = {
        **valid_report,
        "reused_shards_count": 1,
        "shards": [{**valid_shard, "reused": True}],
    }
    att_reused = verify_fresh_junit_shards_attestation_v1793124(
        reused_report,
        expected_tested_executable_sha="a" * 40,
        min_mtime_ns=mtime_ns - 100_000_000,
    )
    assert att_reused["passed"] is False
    assert any("SHARD_REUSED_FORBIDDEN" in v for v in att_reused["violations"])

    # Negative control 2: Wrong SHA must fail RED
    wrong_sha_report = {
        **valid_report,
        "shards": [{**valid_shard, "tested_executable_sha": "b" * 40}],
    }
    att_wrong_sha = verify_fresh_junit_shards_attestation_v1793124(
        wrong_sha_report,
        expected_tested_executable_sha="a" * 40,
        min_mtime_ns=mtime_ns - 100_000_000,
    )
    assert att_wrong_sha["passed"] is False
    assert any("SHARD_SHA_MISMATCH" in v for v in att_wrong_sha["violations"])

    # Negative control 3: Stale XML mtime older than run_start_epoch_ns must fail RED
    att_stale_mtime = verify_fresh_junit_shards_attestation_v1793124(
        valid_report,
        expected_tested_executable_sha="a" * 40,
        min_mtime_ns=mtime_ns + 500_000_000,
    )
    assert att_stale_mtime["passed"] is False
    assert any("SHARD_STALE_MTIME_BEFORE_RUN_START" in v for v in att_stale_mtime["violations"])


def test_v1793124_r5_si7_vs_si8_distinct_replayed_outcomes() -> None:
    from tools.run_v17_9_3_1_2_2_entry_signal_backtest import replay_single_si_policy_v1793122

    datasets, _ = load_canonical_xauusdt_datasets()
    disc = discover_research_candidates_v1793123(datasets, sample_indices_1m=(5000, 5577, 5600, 6075, 6324, 6490))

    cand_index = {
        "decisions_by_checkpoint_index": {},
        "raw_eligible_decisions_count": 0,
        "unique_eligible_timestamps_count": 0,
        "canonical_opportunities": [],
        "baseline_outcomes_by_opp_id": {},
        "si8_discovered_decisions_by_checkpoint_index": disc["si8_discovered_decisions_by_checkpoint_index"],
        "si8_discovered_feature_ledger_by_opp_id": disc["si8_discovered_feature_ledger_by_opp_id"],
    }

    res_si7 = replay_single_si_policy_v1793122("SI7_CORE_TRADING_LOGIC_REPAIRED", cand_index, datasets, {})
    res_si8 = replay_single_si_policy_v1793122("SI8_RESEARCH_DISCOVERY_REPAIRED", cand_index, datasets, {})

    assert res_si7["raw_eligible_decisions_count"] == 0
    assert res_si8["raw_eligible_decisions_count"] > 0
    assert res_si8["unique_eligible_timestamps_evaluated"] > res_si7["unique_eligible_timestamps_evaluated"]
    assert res_si8["accepted_modified_executable_geometry_count"] > 0
    assert res_si8["primary_ledger_sha256"] != res_si7["primary_ledger_sha256"]
    for fm in ("NEXT_BAR_OPEN_BID_ASK", "LEGACY_CLOSE_FILL", "STRESS_WIDER_SPREAD_SLIPPAGE_2X"):
        assert (
            res_si8["replays_by_fill_mode"][fm]["ledger_sha256"]
            != res_si7["replays_by_fill_mode"][fm]["ledger_sha256"]
        )


def test_v1793124_r6_acceptance_gates_g01_g17_and_evidence_mutations() -> None:
    from tools.run_v17_9_3_1_2_4_verification import (
        evaluate_v1793124_acceptance_gates_g01_g17,
        run_evidence_gate_mutation_tests_v1793124,
    )

    sha = "a" * 40
    fake_ev = {
        "01": {"current_branch": "feature/v17-9-2-structural-sl-tp-from-qa", "ancestry_verified": True, "tested_executable_sha": sha},
        "02": {"violated_files_count": 0},
        "04": {
            "si8_canonical_mode": "RESEARCH_APPLY",
            "si8_canonical_policy_id": "SI8_RESEARCH_DISCOVERY_REPAIRED",
            "legacy_mode_mismatch_reproduction": {"fail_closed_verified": True},
            "si7_rejected_baseline_rescued_count": 0,
            "si8_rejected_baseline_rescued_count": 0,
            "off_parity_mismatch_count": 0,
            "observe_only_action_mismatch_count": 0,
            "production_research_apply_blocked_verified": True,
        },
        "05": {
            "si8_newly_discovered_setups_count": 45,
            "si8_incremental_eligible_vs_si7_count": 45,
            "funnel_by_model": {
                m: {"observed": 40, "descriptor_found": 5, "setup_valid": 4, "eligible_after_risk": 3, "incremental_vs_si7": 2}
                for m in CANONICAL_5_MODELS_V1793123
            },
        },
        "06": {
            "si7_vs_si8_distinct_replayed_outcomes_verified": True,
            "si7_primary_ledger_sha256": "1" * 64,
            "si8_primary_ledger_sha256": "2" * 64,
            "si8_incremental_selected_after_arbitration_count": 12,
        },
        "08": {"tested_executable_sha": sha, "collected_node_ids_count": 5000},
        "09": {
            "tested_executable_sha": sha,
            "executed_junit_node_ids_count": 5000,
            "reused_shards_count": 0,
            "freshly_executed_shards_count": 20,
            "shard_count": 20,
            "failed_count": 0,
            "error_count": 0,
            "exact_node_id_bijection_verified": True,
        },
        "10": {"all_negative_controls_rejected_red": True},
        "11": {"executable_mutations_count": 16, "executable_mutations_caught_count": 16, "all_mutations_caught_red_and_restored_green": True},
        "13": {
            "tested_executable_sha": sha,
            "lookahead_violations_count": 0,
            "pending_backdated_fill_violations": 0,
            "missing_htf_no_default_long_verified": True,
            "circular_sl_equals_protected_anchor_tautology_count": 0,
            "unswept_liquidity_pool_target_verified": True,
            "planned_risk_cap_violations_count": 0,
            "min_net_rr_violations_count": 0,
            "key_collapse_count": 0,
            "followup_truncation_cap": None,
            "unique_eligible_timestamps_visited": 18744,
            "strict_hold_phantom_slot_release_violations": 0,
            "explicit_180_bar_time_stop_pays_fees_and_spread": True,
            "data_gap_remains_censored_verified": True,
            "metrics_by_policy": {
                "SI0_OFF_BASELINE": {
                    "closed_trades_count": 166,
                    "wins_count": 9,
                    "losses_count": 157,
                    "open_at_horizon_count": 74,
                    "total_realized_net_usd": -345.8971,
                },
                "SI8_RESEARCH_DISCOVERY_REPAIRED": {
                    "unique_eligible_timestamps_evaluated": 18758,
                },
            },
        },
        "14": {
            "fill_modes_replayed": ["NEXT_BAR_OPEN_BID_ASK", "LEGACY_CLOSE_FILL", "STRESS_WIDER_SPREAD_SLIPPAGE_2X"],
            "ledger_accounting_identity_violations": 0,
            "all_1_2_4_worker_hashes_identical": True,
            "independent_execution_no_shared_digest_reuse": True,
            "worker_1_sha256": "3" * 64,
            "per_policy_hash_parity": {
                "SI0_OFF_BASELINE": {"identical": True},
                "SI5_STRUCTURAL_ENTRY_SL_AND_REACHABLE_TARGET": {"identical": True},
                "SI7_CORE_TRADING_LOGIC_REPAIRED": {"identical": True},
                "SI8_RESEARCH_DISCOVERY_REPAIRED": {"identical": True},
            },
        },
        "15": {"fresh_oos_verification_status": "REUSED_HISTORICAL_HOLDOUT_NOT_FRESH_OOS"},
        "17": {
            "strategy_statistical_verdict": "NEGATIVE_EXPECTANCY",
            "activation_ready": "NO",
            "verdict_computed_from_metrics_not_hardcoded": True,
        },
        "18": {
            "branch": "feature/v17-9-2-structural-sl-tp-from-qa",
            "phase_a_tested_code_sha": sha,
            "phase_a_tested_sha_verified": True,
        },
    }

    egm = run_evidence_gate_mutation_tests_v1793124(
        fake_ev,
        tested_executable_sha=sha,
        dataset_sha256="9f301fee2e62d3eabce1aa2eed0ebcd8b2fbce4f8db8b66f9d87fd2f26500163",
    )
    assert egm["all_caught"] is True
    assert egm["evidence_gate_mutations_count"] == 17

    fake_ev["12"] = egm
    gates = evaluate_v1793124_acceptance_gates_g01_g17(
        evidence_by_key=fake_ev,
        tested_executable_sha=sha,
        dataset_sha256="9f301fee2e62d3eabce1aa2eed0ebcd8b2fbce4f8db8b66f9d87fd2f26500163",
    )
    assert gates["all_17_acceptance_gates_passed"] is True
    assert gates["passed_gates_count"] == 17
