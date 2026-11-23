"""
aurum-desk/backend/tests/test_kh03_r3_research_falsification.py

KH-03.R3 P1-C Causal Feature, Pre-Registration & Candidate Conservation Tests:
Proves M5 unswept target lookup NEVER reads future or already-swept levels,
pre-registration plan SHA hashes strictly before validation with H3 excluded and m=3,
E1/E2/E3 candidate streams satisfy exact `accepted + rejected == base_eligible`,
and E2 delayed confirmation strictly enforces `new_decision_ts > base_decision_ts` (no OPEN_TO_OPEN).
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
    KH03_R3_PRIMARY_ARMS,
    build_r3_causal_m5_unswept_target_ledger,
    build_r3_train_only_candidates_asof,
    freeze_r3_bounded_research_plan,
    query_nearest_causal_unswept_m5_target,
)
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash


def test_prereg_plan_hashes_deterministically_and_excludes_h3():
    p = freeze_r3_bounded_research_plan(tested_code_sha="a" * 40)
    assert p["status"] == "PASS"
    assert p["h3_r2_excluded_from_executable_arms"] is True
    assert p["finite_search_budget"]["declared_primary_arms_count"] == 3
    assert p["OOS_EVALUATIONS"] == 0
    assert p["ACTIVATION_READY"] == "NO"
    recomp = canonical_hash({k: v for k, v in p.items() if k != "plan_frozen_sha256"})
    assert p["plan_frozen_sha256"] == recomp


def test_m5_unswept_target_strictly_ignores_future_and_already_swept_levels():
    costs = CostAssumptions()
    # Future swing confirmed AFTER decision_ts_ms -> MUST be ignored
    fut_led = {"swings_high": [(1791500060000, 4120.0, 99999)], "swings_low": []}
    q1 = query_nearest_causal_unswept_m5_target(
        m5_ledger=fut_led, direction="LONG", decision_idx_1m=100, decision_ts_ms=1791500000000,
        entry_price=4100.0, selected_sl=4095.0, baseline_tp=4140.0, costs=costs,
    )
    assert q1["replaced_baseline_tp"] is False and q1["selected_tp"] == 4140.0

    # Already swept at bar 90 <= decision_idx_1m 100 -> MUST be ignored
    sw_led = {"swings_high": [(1791400000000, 4120.0, 90)], "swings_low": []}
    q2 = query_nearest_causal_unswept_m5_target(
        m5_ledger=sw_led, direction="LONG", decision_idx_1m=100, decision_ts_ms=1791500000000,
        entry_price=4100.0, selected_sl=4095.0, baseline_tp=4140.0, costs=costs,
    )
    assert q2["replaced_baseline_tp"] is False

    # Strictly as-of confirmed & unswept with net_rr >= 2.0 -> admitted
    ok_led = {"swings_high": [(1791400000000, 4125.0, 250)], "swings_low": []}
    q3 = query_nearest_causal_unswept_m5_target(
        m5_ledger=ok_led, direction="LONG", decision_idx_1m=100, decision_ts_ms=1791500000000,
        entry_price=4100.0, selected_sl=4095.0, baseline_tp=4140.0, costs=costs,
    )
    assert q3["replaced_baseline_tp"] is True and q3["selected_tp"] == 4125.0


def test_r3_candidates_exact_conservation_and_e2_causal_delay_invariant():
    ds, _ = load_r2_canonical_dataset_readonly(REPO_ROOT / CANONICAL_DATASET_REL_PATH)
    end_ts = int(ds["1m"][3499]["close_time"])
    ds_sub = {k: [b for b in ds.get(k, []) if int(b["open_time"]) <= end_ts] for k in ("1m", "5m", "15m", "1H", "4H", "1D")}
    ds_sub["1m"] = list(ds["1m"])[:3500]
    mtf = build_kh02_asof_mtf_features(ds_sub)
    led = build_r2_causal_unswept_level_ledger(ds_sub, source_latency_ms=0, warmup_m1=30, run_t10_future_mutation_proof=False, rng_seed=20261121)
    r2c = build_r2_target_repair_full_opportunity_conservation(
        datasets=ds_sub, mtf_bundle=mtf, ledger_bundle=led, r1_candidates=None, cost_assumptions=CostAssumptions()
    )
    m5_led = build_r3_causal_m5_unswept_target_ledger(datasets=ds_sub, pivot_radius=2)
    r3c = build_r3_train_only_candidates_asof(
        datasets=ds_sub, mtf_bundle=mtf, r2_cands_bundle=r2c, m5_ledger=m5_led, costs=CostAssumptions()
    )
    assert r3c["all_arms_conserved_exact"] is True
    for ac in KH03_R3_PRIMARY_ARMS:
        assert r3c["arm_conservation"][ac]["conservation_exact"] is True

    # E2 delayed confirmation must ALWAYS advance timestamp strictly past base decision!
    e2_cands = r3c["arm_candidates"]["KH03_R3_E2_CAUSAL_DELAYED_CONFIRMATION"]
    assert len(e2_cands) > 0
    for c in e2_cands:
        assert int(c["decision_timestamp_ms"]) > int(c["base_decision_timestamp_ms"])
        assert 1 <= int(c["wait_closed_bars"]) <= 6
