"""
aurum-desk/backend/tests/test_kh_02_entry_foundation_research.py

Unit and integration tests for KH-02 Entry Foundation Research service:
  - Immutable baseline registry & runtime OFF hard safety locks
  - Pre-registered hypothesis freeze & post-freeze tamper rejection
  - Full-universe Foundation-B2 candidate generation (no survivorship bias, risk <= $2.50, net R:R >= 2.0)
  - Chronological 1-position 3-fill replay & non-overlap verification
  - 1x/2x/3x cost stress & Decimal 0.04% vs 0.06% fee counterfactuals
  - Matched cohorts, ablations, negative controls & cluster uncertainty / zero-trade honesty
  - OOS escrow zero-evaluation & non-contamination enforcement
"""
from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import sys
from typing import Any, Dict, List

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = BACKEND_ROOT.parent
REPO_ROOT = APP_ROOT.parent
for _p in (str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.kh_02_entry_foundation_research_service import (
    CANONICAL_DATASET_SHA256_KH02,
    MANDATORY_BRANCH_KH02,
    assert_kh02_runtime_safety_modes_off,
    audit_kh02_dataset_registry_and_quality,
    audit_kh02_oos_escrow_and_noncontamination,
    build_kh02_asof_mtf_features,
    build_kh02_matched_cohorts_and_ablations,
    estimate_kh02_edge_uncertainty,
    generate_kh02_research_candidates,
    load_kh02_immutable_baseline_registry,
    preregister_kh02_hypotheses,
    replay_kh02_chronological_one_position,
    stress_kh02_execution_and_costs,
    verify_chronological_non_overlap,
    verify_kh02_plan_integrity_unchanged,
)
from tools.run_v17_9_3_1_2_1_full_opportunity_replay import DATASET_PATH
from tools.run_v17_9_3_1_2_full_universe_backtest import load_canonical_xauusdt_datasets


def test_kh02_immutable_registry_and_runtime_safety_locks() -> None:
    guard = assert_kh02_runtime_safety_modes_off(check_live_scalp_config=True)
    assert guard["status"] == "PASS"
    assert guard["ACTIVATION_READY"] == "NO"
    assert all(v == "OFF" for v in guard["runtime_safety_modes"].values())

    for bad_key in ("LIVE", "PAPER", "AUTO", "PRODUCTION"):
        with pytest.raises(PermissionError):
            assert_kh02_runtime_safety_modes_off(
                {"PRODUCTION": "OFF", "LIVE": "OFF", "PAPER": "OFF", "AUTO": "OFF", "ACTIVATION_READY": "NO", bad_key: "ON"},
                check_live_scalp_config=False,
            )

    reg = load_kh02_immutable_baseline_registry(REPO_ROOT, "1234567890ab")
    assert reg.verification_passed is True
    assert reg.branch == MANDATORY_BRANCH_KH02
    assert reg.canonical_dataset_sha256 == CANONICAL_DATASET_SHA256_KH02
    assert reg.a1_candidate_count == 56710
    assert reg.si8_discovery_count == 6375
    assert reg.si0_reference_scheduled == 242
    assert reg.si0_reference_closed == 166
    assert reg.si0_reference_net_pnl_usd == -345.8971


def test_kh02_preregistration_freeze_budget_embargo_and_tamper_detection() -> None:
    plan = preregister_kh02_hypotheses(
        dataset_sha256=CANONICAL_DATASET_SHA256_KH02,
        config_sha256="cfg_sha_test",
        code_sha256="code_sha_test",
        purged_embargo_minutes=240,
        max_holding_horizon_minutes=180,
    )
    assert plan.frozen_before_outcome_evaluation is True
    assert plan.primary_hypotheses_count == 3
    assert plan.max_planned_risk_usd == 2.50
    assert plan.min_net_rr == 2.0
    assert verify_kh02_plan_integrity_unchanged(plan, asdict(plan))["status"] == "PASS"

    # Mutate plan post-freeze -> FAIL
    mut = asdict(plan)
    mut["min_net_rr"] = 1.25
    assert verify_kh02_plan_integrity_unchanged(plan, mut)["status"] == "FAIL"

    # Embargo < holding horizon -> ValueError
    with pytest.raises(ValueError, match="KH02_EMBARGO_LT_HOLDING_HORIZON"):
        preregister_kh02_hypotheses(
            dataset_sha256="ds", config_sha256="cfg", code_sha256="code",
            purged_embargo_minutes=60, max_holding_horizon_minutes=180,
        )

    # Budget > 3 primary hypotheses -> ValueError
    extra_prim = list(plan.primary_hypotheses) + [dict(plan.primary_hypotheses[0], hypothesis_id="H4", arm_code="B2_H4", rule={"type": "EXTRA"})]
    with pytest.raises(ValueError, match="KH02_PRIMARY_HYPOTHESIS_BUDGET_VIOLATION"):
        preregister_kh02_hypotheses(
            dataset_sha256="ds", config_sha256="cfg", code_sha256="code",
            primary_hypotheses=extra_prim,
        )


def test_kh02_end_to_end_real_dataset_slice_replay_stress_uncertainty_and_oos() -> None:
    from tools.run_kh_01a_verification import file_sha256

    datasets, _ds_meta = load_canonical_xauusdt_datasets()
    ds_sha = file_sha256(DATASET_PATH)
    assert ds_sha == CANONICAL_DATASET_SHA256_KH02

    q_rep = audit_kh02_dataset_registry_and_quality(
        datasets, dataset_path=DATASET_PATH, dataset_sha256=ds_sha
    )
    assert q_rep["status"] == "PASS"
    assert q_rep["data_origin_enum"]["l1_bid_ask_quotes"] == "SIMULATED"
    assert q_rep["mtf_m15_h1_covers_m1_window"] is True

    # Run slice across first 3,000 M1 bars to test full B2 pipeline deterministically
    ds_slice = {"1m": datasets["1m"][:3000], "15m": datasets["15m"], "1H": datasets["1H"]}
    mtf = build_kh02_asof_mtf_features(ds_slice, warmup_m1=30)
    assert mtf["invalidation_violations_count"] == 0
    assert mtf["post_warmup_bar_count"] == 2970

    plan = preregister_kh02_hypotheses(dataset_sha256=ds_sha, config_sha256="cfg", code_sha256="code")
    gen = generate_kh02_research_candidates(mtf, plan)
    assert gen["total_directional_bar_opportunities"] == 2970 * 2
    assert gen["raw_m1_triggers_count"] > 0
    assert gen["raw_geometry_risk_eligible_count"] > 0

    bars_1m = list(ds_slice["1m"])
    replays: Dict[str, Any] = {}
    for ac in ("B2_H1", "B2_H2", "B2_H3", "NC_NULL_TRIGGER"):
        rep = replay_kh02_chronological_one_position(ac, gen["arm_conservation"][ac]["variant_id"], gen["arm_candidates"][ac], bars_1m)
        assert rep["overlap_violations_count"] == 0
        assert rep["risk_cap_violations_count"] == 0
        assert rep["min_net_rr_violations_count"] == 0
        assert verify_chronological_non_overlap(rep["episodes_by_mode"]["NEXT_BAR_OPEN_BID_ASK"])["status"] == "PASS"
        replays[ac] = rep

    st = stress_kh02_execution_and_costs(replays["NC_NULL_TRIGGER"])
    assert st["quote_mode"] == "MODELED_BID_ASK"
    assert st["actual_account_effective_fee_tier"] == "UNKNOWN_NOT_LOCALLY_VERIFIED"

    unc = estimate_kh02_edge_uncertainty(replays["NC_NULL_TRIGGER"], st, [], fresh_oos_verified=False)
    assert unc["arm_economic_verdict"] != "PROVEN_EDGE"
    assert unc["fresh_oos_verdict"] == "BLOCKED_NO_UNTOUCHED_DATA"
    assert unc["activation_ready"] == "NO"

    # Matched cohorts & ablations
    mc = build_kh02_matched_cohorts_and_ablations(
        a1_decisions=[], b0_nb_episodes=[], mtf_bundle=mtf,
        b2_replay_by_arm=replays, b2_conservation_by_arm=gen["arm_conservation"],
    )
    assert len(mc["foundation_b2_ablations_and_negative_controls"]) == 4

    # OOS escrow blocks reused canonical SHA and enforces 0 evaluations
    oos_ok = audit_kh02_oos_escrow_and_noncontamination(
        freeze_timestamp_ms=1_795_300_000_000, tested_executable_sha="abcd12345678", frozen_config_sha256="cfg",
    )
    assert oos_ok["status"] == "PASS"
    assert oos_ok["OOS_EVALUATIONS"] == 0
    assert oos_ok["FRESH_OOS_VERDICT"] == "BLOCKED_NO_UNTOUCHED_DATA"
