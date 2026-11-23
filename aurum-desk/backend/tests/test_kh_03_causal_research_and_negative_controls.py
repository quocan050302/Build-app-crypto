"""
aurum-desk/backend/tests/test_kh_03_causal_research_and_negative_controls.py

KH-03.R1 Pre-Registration Freeze, Future-Mutation Invariance, Opportunity Conservation,
Paired Null Matching, Placebo/Time-Offset Negative Controls, 6-Scenario Cost Stress,
Holm-Bonferroni Multiplicity & Honest DEV Verdict Tests.
"""
from __future__ import annotations

from pathlib import Path
import sys

APP_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = APP_ROOT.parent
BACKEND_ROOT = APP_ROOT / "backend"
for _p in (str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.kh_02_entry_foundation_research_service import (
    build_kh02_asof_mtf_features,
    generate_kh02_research_candidates,
)
from services.kh_02_r2_research_integrity_service import (
    _r2_frozen_plan_and_arms,
    build_r2_causal_unswept_level_ledger,
)
from services.kh_03_research_service import (
    KH03_ALL_ARMS,
    KH03_PRIMARY_ARMS,
    _holm_bonferroni_adjust,
    preregister_kh03_research_plan,
    run_kh03_full_research_study_10_to_16,
)


def test_kh03_holm_bonferroni_and_prereg_plan_integrity() -> None:
    adj = _holm_bonferroni_adjust({"A": 0.01, "B": 0.04, "C": 0.20})
    assert adj["A"] == 0.03
    assert adj["B"] == 0.08
    assert adj["C"] == 0.20

    plan = preregister_kh03_research_plan(tested_code_sha="a" * 40)
    assert plan["status"] == "PASS"
    assert plan["written_before_any_kh03_replay"] is True
    assert plan["OOS_EVALUATIONS"] == 0
    assert plan["ACTIVATION_READY"] == "NO"
    assert plan["finite_search_budget"]["max_primary_arms"] == 3
    assert len(plan["plan_frozen_sha256"]) == 64


def test_kh03_full_causal_research_pipeline_and_negative_controls_on_synthetic() -> None:
    from tests.test_kh_02_r2_counterfactual_and_costs import _synth_multi_tf

    ds = _synth_multi_tf(420)
    mtf = build_kh02_asof_mtf_features(ds, warmup_m1=30)
    r1_plan, _ = _r2_frozen_plan_and_arms(code_sha="b" * 40)
    r1_cands = generate_kh02_research_candidates(mtf, r1_plan)
    led = build_r2_causal_unswept_level_ledger(ds, warmup_m1=30, run_t10_future_mutation_proof=True)
    plan = preregister_kh03_research_plan(tested_code_sha="b" * 40)

    docs = run_kh03_full_research_study_10_to_16(
        datasets=ds,
        mtf_bundle=mtf,
        ledger_bundle=led,
        r1_candidates=r1_cands,
        tested_code_sha="b" * 40,
        prereg_plan=plan,
    )
    assert docs["11"]["status"] == "PASS"
    assert docs["11"]["future_mutation_invariance"]["passed"] is True
    assert docs["11"]["future_mutation_invariance"]["mismatches_count"] == 0

    assert docs["12"]["status"] == "PASS"
    assert docs["12"]["all_arms_conserved_exact"] is True
    assert docs["12"]["total_overlap_violations"] == 0
    assert set(docs["12"]["arm_1x_replay_summaries"].keys()) == set(KH03_ALL_ARMS)

    assert docs["13"]["status"] == "PASS"
    assert docs["13"]["b0_vs_b2_cross_universe_status"] == "COMPARISON_NOT_IDENTIFIABLE"
    assert set(docs["13"]["arm_matching_reports"].keys()) == set(KH03_PRIMARY_ARMS)

    assert docs["14"]["status"] == "PASS"
    assert docs["14"]["zero_fee_display_bugs_count"] == 0
    assert len(docs["14"]["scenarios"]) == 6

    f15 = docs["15"]
    assert f15["status"] == "PASS"
    assert f15["OOS_EVALUATIONS"] == 0
    assert f15["ACTIVATION_READY"] == "NO"
    assert f15["RESEARCH_ECONOMIC_VERDICT"] == "NO_PROVEN_EDGE"
    assert f15["FRESH_OOS_VERDICT"] == "BLOCKED_NO_UNTOUCHED_DATA"
    assert f15["preregistered_plan_sha256"] == plan["plan_frozen_sha256"]

    f16 = docs["16"]
    assert f16["status"] == "PASS"
    assert f16["planned_cap_is_physical_guarantee"] is False
    assert "NOT a physical guarantee" in f16["realized_tail_loss_disclosure"]
