"""
aurum-desk/backend/tests/test_kh03_r3_forward_only_economics.py

KH-03.R3 Forward-Only Fold Evaluation, Six-Scenario Cost Stress & Strict DEV Gate Tests:
Exercises R3 E1/E2/E3 end-to-end on a DEV slice through R2 expanding forward-only folds,
verifies future-win injection invariance, fixed-fill vs dynamic re-risk separation,
paired E3 vs NULL accounting, strict R2 CI + Holm + synthetic positive capability check,
and zero OOS evaluations / NO_PROVEN_EDGE lock.
"""
from __future__ import annotations

from pathlib import Path
import sys

APP_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = APP_ROOT / "backend"
REPO_ROOT = APP_ROOT.parent
for _p in (str(BACKEND_ROOT), str(APP_ROOT), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from domain_calculator import CostAssumptions
from services.kh_02_entry_foundation_research_service import build_kh02_asof_mtf_features
from services.kh_02_r2_research_integrity_service import (
    CANONICAL_DATASET_REL_PATH,
    build_r2_causal_unswept_level_ledger,
    build_r2_target_repair_full_opportunity_conservation,
    load_r2_canonical_dataset_readonly,
)
from services.kh_03_r3_experiment_service import (
    build_r3_causal_m5_unswept_target_ledger,
    build_r3_train_only_candidates_asof,
    freeze_r3_bounded_research_plan,
    run_r3_forward_only_dev_evaluation,
    run_r3_paired_and_strict_dev_evaluation_15_16,
)


def test_r3_forward_only_wf_and_six_cost_strict_dev_gate_end_to_end():
    ds, _ = load_r2_canonical_dataset_readonly(REPO_ROOT / CANONICAL_DATASET_REL_PATH)
    end_ts = int(ds["1m"][5999]["close_time"])
    ds_sub = {k: [b for b in ds.get(k, []) if int(b["open_time"]) <= end_ts] for k in ("1m", "5m", "15m", "1H", "4H", "1D")}
    ds_sub["1m"] = list(ds["1m"])[:6000]
    mtf = build_kh02_asof_mtf_features(ds_sub)
    led = build_r2_causal_unswept_level_ledger(ds_sub, source_latency_ms=0, warmup_m1=30, run_t10_future_mutation_proof=False, rng_seed=20261121)
    r2c = build_r2_target_repair_full_opportunity_conservation(
        datasets=ds_sub, mtf_bundle=mtf, ledger_bundle=led, r1_candidates=None, cost_assumptions=CostAssumptions()
    )
    m5_led = build_r3_causal_m5_unswept_target_ledger(datasets=ds_sub, pivot_radius=2)
    f11 = freeze_r3_bounded_research_plan(tested_code_sha="d" * 40)
    r3c = build_r3_train_only_candidates_asof(
        datasets=ds_sub, mtf_bundle=mtf, r2_cands_bundle=r2c, m5_ledger=m5_led, costs=CostAssumptions()
    )

    f13, f14 = run_r3_forward_only_dev_evaluation(
        datasets=ds_sub, mtf_bundle=mtf, r3_cands_1x=r3c, prereg_plan_11=f11, tested_code_sha="d" * 40
    )
    assert f13["status"] == "PASS"
    assert f13["is_fresh_oos"] is False and f13["OOS_EVALUATIONS"] == 0
    assert f13["future_win_injection_invariance_passed"] is True
    assert f14["status"] == "PASS" and f14["total_overlap_violations"] == 0

    f15, f16 = run_r3_paired_and_strict_dev_evaluation_15_16(
        datasets=ds_sub, mtf_bundle=mtf, ledger_bundle=led, m5_ledger=m5_led,
        r2_cands_1x=r2c, r3_cands_1x=r3c, prereg_plan_11=f11, wf_13=f13, wf_14=f14,
    )
    assert f15["status"] == "PASS"
    assert len(f15["dynamic_full_rerisk_six_scenarios"]) == 6
    assert f15["zero_fee_display_bugs_count"] == 0
    assert f15["observed_l1_bbo_available"] is False
    assert f16["status"] == "PASS"
    assert f16["synthetic_positive_gate_capability_proof"]["passes_preregistered_dev_advancement"] is True
    assert f16["RESEARCH_ECONOMIC_VERDICT"] == "NO_PROVEN_EDGE"
    assert f16["OOS_EVALUATIONS"] == 0 and f16["ACTIVATION_READY"] == "NO"
