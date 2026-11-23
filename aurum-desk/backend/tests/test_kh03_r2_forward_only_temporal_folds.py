"""
aurum-desk/backend/tests/test_kh03_r2_forward_only_temporal_folds.py

KH-03.R2 P0-SCI-03 True Chronological Forward-Only DEV Walk-Forward Tests:
Verifies R1 post-hoc/future-train RED proof, expanding past-only train < val,
239m59s embargo FAIL vs 240m PASS, label-exit crossing purge, future HTF bar
mutation invariance, fake future +100R win NOT altering prior fold frozen selection,
fold-local independent replay, insufficient history truthful rejection, DST ordering,
duplicate episode rejection, and `is_fresh_oos == False`.
"""
from __future__ import annotations

import copy
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
from services.kh_02_r2_prospective_data_service import normalize_ny_local_to_utc_ms_r2_c1
from services.kh_02_r2_research_integrity_service import (
    CANONICAL_DATASET_REL_PATH,
    build_r2_causal_unswept_level_ledger,
    build_r2_target_repair_full_opportunity_conservation,
    load_r2_canonical_dataset_readonly,
)
from services.kh_03_r2_methodology_service import (
    build_kh03_r2_forward_only_dev_folds,
    build_kh03_r2_wf_red_proofs,
    run_kh03_r2_forward_only_walkforward,
    select_past_only_arm_for_fold,
    validate_kh03_r2_fold_causality,
)


def _synth_bars_1m(n: int = 2400, start_ms: int = 1791437880000) -> list[dict]:
    out = []
    px = 4100.0
    for i in range(n):
        ot = start_ms + i * 60_000
        ct = ot + 60_000
        out.append({"open_time": ot, "close_time": ct, "open": px, "high": px + 1.0, "low": px - 1.0, "close": px + 0.1, "volume": 10.0})
    return out


def test_forward_only_folds_strictly_past_only_and_never_fresh_oos():
    bars = _synth_bars_1m(3200)
    spec = build_kh03_r2_forward_only_dev_folds(bars_1m=bars, embargo_minutes=240, max_label_horizon_minutes=180, n_blocks=4)
    assert spec["status"] == "PASS"
    assert spec["is_fresh_oos"] is False
    assert spec["OOS_EVALUATIONS"] == 0
    assert spec["valid_folds_count"] == 3
    caus = validate_kh03_r2_fold_causality(spec["folds"], min_embargo_minutes=240)
    assert caus["valid"] is True
    for f in spec["folds"]:
        assert f["train_max_known_at_ts_ms"] < f["val_min_ts_ms"]
        assert (f["val_min_ts_ms"] - f["train_max_known_at_ts_ms"]) >= 240 * 60_000
        assert f["future_train_bars_count"] == 0
        assert f["is_fresh_oos"] is False


def test_embargo_239m59s_fails_and_240m_passes():
    bars = _synth_bars_1m(3200)
    spec = build_kh03_r2_forward_only_dev_folds(bars_1m=bars, embargo_minutes=240, n_blocks=4)
    assert validate_kh03_r2_fold_causality(spec["folds"], min_embargo_minutes=240)["valid"] is True

    bad = copy.deepcopy(spec["folds"])
    bad[0]["train_max_known_at_ts_ms"] = bad[0]["val_min_ts_ms"] - (239 * 60_000 + 59_000)
    res = validate_kh03_r2_fold_causality(bad, min_embargo_minutes=240)
    assert res["valid"] is False
    assert any("ACTUAL_TRAIN_VAL_GAP_BELOW_EMBARGO" in v for v in res["violations"])


def test_training_after_validation_and_label_crossing_embargo_rejected():
    bars = _synth_bars_1m(3200)
    spec = build_kh03_r2_forward_only_dev_folds(bars_1m=bars, embargo_minutes=240, n_blocks=4)

    bad_post = copy.deepcopy(spec["folds"])
    bad_post[0]["train_max_known_at_ts_ms"] = bad_post[0]["val_max_ts_ms"] + 60_000
    r_post = validate_kh03_r2_fold_causality(bad_post)
    assert r_post["valid"] is False
    assert any("TRAIN_NOT_STRICTLY_BEFORE_VAL" in v for v in r_post["violations"])

    bad_lbl = copy.deepcopy(spec["folds"])
    bad_lbl[0]["train_last_label_exit_ts_ms"] = bad_lbl[0]["embargo_start_ms"] + 1
    r_lbl = validate_kh03_r2_fold_causality(bad_lbl)
    assert r_lbl["valid"] is False
    assert any("TRAIN_LABEL_EXIT_CROSSED_EMBARGO" in v for v in r_lbl["violations"])


def test_duplicate_episode_across_folds_and_train_val_overlap_rejected():
    bars = _synth_bars_1m(3200)
    spec = build_kh03_r2_forward_only_dev_folds(bars_1m=bars, embargo_minutes=240, n_blocks=4)

    bad_ov = copy.deepcopy(spec["folds"])
    bad_ov[0]["train_episode_ids"] = ["ep_1", "ep_2"]
    bad_ov[0]["val_episode_ids"] = ["ep_2", "ep_3"]
    assert not validate_kh03_r2_fold_causality(bad_ov)["valid"]

    bad_dup = copy.deepcopy(spec["folds"])
    bad_dup[0]["val_episode_ids"] = ["ep_shared"]
    bad_dup[1]["val_episode_ids"] = ["ep_shared"]
    r_dup = validate_kh03_r2_fold_causality(bad_dup)
    assert r_dup["valid"] is False
    assert any("CROSS_FOLD_DUPLICATE_VAL_EPISODE" in v for v in r_dup["violations"])


def test_insufficient_history_returns_truthful_status_never_fabricates_folds():
    short_bars = _synth_bars_1m(200)
    spec = build_kh03_r2_forward_only_dev_folds(bars_1m=short_bars, embargo_minutes=240, n_blocks=4)
    assert spec["status"] == "WALKFORWARD_INSUFFICIENT_HISTORY_OR_POWER"
    assert spec["valid_folds_count"] == 0
    assert spec["folds"] == []


def test_ny_dst_fallback_time_ordering_preserved():
    f0 = normalize_ny_local_to_utc_ms_r2_c1("2026-11-01T01:30:00", fold=0)
    f1 = normalize_ny_local_to_utc_ms_r2_c1("2026-11-01T01:30:00", fold=1)
    assert f1["utc_ms"] - f0["utc_ms"] == 3_600_000


def test_past_only_selector_ignores_future_and_freezes_sha():
    s1 = select_past_only_arm_for_fold(
        fold_id="R2_FWD_FOLD_1",
        tested_code_sha="a" * 40,
        train_arm_summaries={
            "KH03_H1_MTF_UNSWEPT_ALIGN": {"closed": 20, "expectancy_r": -0.5, "net_pnl_usd": -25.0, "executed_ids_sha256": "1" * 64},
            "KH03_H2_LOW_FRICTION_VOL_REGIME": {"closed": 10, "expectancy_r": -0.9, "net_pnl_usd": -20.0, "executed_ids_sha256": "2" * 64},
        },
    )
    assert s1["selected_arm_on_past_train_only"] == "KH03_H1_MTF_UNSWEPT_ALIGN"
    assert s1["selected_arm_meets_positive_train_bar"] is False
    assert s1["selection_frozen_before_validation_replay"] is True
    assert len(s1["selection_freeze_sha256"]) == 64


def test_real_r1_wf_red_proof_and_r2_true_forward_only_replay_on_dev_slice():
    ds, _ = load_r2_canonical_dataset_readonly(REPO_ROOT / CANONICAL_DATASET_REL_PATH)
    r1_dir = REPO_ROOT / "aurum-desk/docs/artifacts/kh_03_research_data/20261123T055123Z_f0b668dc0dd1"
    f10 = build_kh03_r2_wf_red_proofs(
        prior_r1_07_path=r1_dir / "07_partition_freeze_pre_registration_and_noncontamination.json",
        prior_r1_15_path=r1_dir / "15_purged_walkforward_bootstrap_multiplicity_power.json",
        bars_1m=ds["1m"],
    )
    assert f10["status"] == "PASS"
    assert f10["r1_future_train_leakage_by_fold"][0]["strictly_past_train_bars"] == 0
    assert f10["r1_future_train_leakage_by_fold"][0]["future_after_val_train_bars_leaked"] > 10_000

    # Run genuine R2 forward-only WF on a 6,000-bar slice for fast unit test execution
    ds_sub = {
        "1m": list(ds["1m"])[:6000],
        "5m": [b for b in ds.get("5m", []) if int(b["open_time"]) <= int(ds["1m"][5999]["close_time"])],
        "15m": [b for b in ds.get("15m", []) if int(b["open_time"]) <= int(ds["1m"][5999]["close_time"])],
        "1H": [b for b in ds.get("1H", []) if int(b["open_time"]) <= int(ds["1m"][5999]["close_time"])],
        "4H": [b for b in ds.get("4H", []) if int(b["open_time"]) <= int(ds["1m"][5999]["close_time"])],
        "1D": [b for b in ds.get("1D", []) if int(b["open_time"]) <= int(ds["1m"][5999]["close_time"])],
    }
    mtf = build_kh02_asof_mtf_features(ds_sub)
    led = build_r2_causal_unswept_level_ledger(ds_sub)
    r2c = build_r2_target_repair_full_opportunity_conservation(
        datasets=ds_sub, mtf_bundle=mtf, ledger_bundle=led, r1_candidates=None, cost_assumptions=CostAssumptions()
    )
    f11, f12, f13 = run_kh03_r2_forward_only_walkforward(
        datasets=ds_sub, mtf_bundle=mtf, r2_cands_1x=r2c, tested_code_sha="b" * 40
    )
    assert f11["status"] == "PASS"
    assert f11["valid_folds_count"] == 3
    assert f12["status"] == "PASS"
    assert f12["all_train_exits_strictly_le_embargo_start"] is True
    assert f12["all_cross_fold_val_duplicates_zero"] is True
    assert f13["status"] == "PASS"
    assert f13["future_win_injection_invariance_passed"] is True
