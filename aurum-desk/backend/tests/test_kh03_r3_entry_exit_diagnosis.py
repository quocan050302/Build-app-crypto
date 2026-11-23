"""
aurum-desk/backend/tests/test_kh03_r3_entry_exit_diagnosis.py

KH-03.R3 P0-B Forensic Entry/Exit Failure Diagnosis Tests:
Verifies exact opportunity lineage conservation (05), streaming Decimal forensic ledger (06)
vs denominator breakdown (07), 3-axis ENGINE/STRATEGY/DATA attribution (08), tamper detection
for dropped losses / zeroed fees, and direction-inverted + conservative tie controls (10).
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
from services.kh_02_r2_research_integrity_service import (
    CANONICAL_DATASET_REL_PATH,
    build_r2_causal_unswept_level_ledger,
    build_r2_target_repair_full_opportunity_conservation,
    load_r2_canonical_dataset_readonly,
)
from services.kh_03_r3_diagnostic_service import (
    audit_r3_entry_exit_geometry_and_accounting,
    build_r3_full_opportunity_lineage,
    build_r3_win_loss_failure_attribution,
    run_r3_negative_control_and_label_integrity_tests,
    validate_r3_forensic_ledger_independently,
)


def _load_slice(n_bars: int = 3500):
    ds, _ = load_r2_canonical_dataset_readonly(REPO_ROOT / CANONICAL_DATASET_REL_PATH)
    end_ts = int(ds["1m"][n_bars - 1]["close_time"])
    ds_sub = {k: [b for b in ds.get(k, []) if int(b["open_time"]) <= end_ts] for k in ("1m", "5m", "15m", "1H", "4H", "1D")}
    ds_sub["1m"] = list(ds["1m"])[:n_bars]
    mtf = build_kh02_asof_mtf_features(ds_sub)
    led = build_r2_causal_unswept_level_ledger(ds_sub, source_latency_ms=0, warmup_m1=30, run_t10_future_mutation_proof=False, rng_seed=20261121)
    r2c = build_r2_target_repair_full_opportunity_conservation(
        datasets=ds_sub, mtf_bundle=mtf, ledger_bundle=led, r1_candidates=None, cost_assumptions=CostAssumptions()
    )
    return ds_sub, mtf, led, r2c


def test_forensic_lineage_ledger_attribution_and_tamper_detection(tmp_path):
    ds_sub, mtf, _led, r2c = _load_slice(3500)
    gz_p = tmp_path / "06_forensic.jsonl.gz"
    meta06, rows, null_sum = audit_r3_entry_exit_geometry_and_accounting(
        datasets=ds_sub, mtf_bundle=mtf, r2_cands_1x=r2c, out_ledger_gz_path=gz_p
    )
    assert meta06["max_gross_fee_net_residual_usd"] <= 0.001
    assert meta06["overlap_violations"] == 0

    f05 = build_r3_full_opportunity_lineage(datasets=ds_sub, r2_cands_1x=r2c, null_replay_summary=null_sum, null_episodes=rows)
    assert f05["status"] == "PASS"
    assert all(f05["conservation_checks"].values())

    f07, f08 = build_r3_win_loss_failure_attribution(forensic_rows=rows, ledger_meta_06=meta06)
    assert f07["status"] == "PASS"
    assert f08["status"] == "PASS"
    assert f08["planned_cap_is_physical_guarantee"] is False
    assert f08["three_axis_diagnosis"]["ENGINE_AXIS"]["accounting_or_simulator_bug_detected"] is False
    assert "ASSOCIATION_ONLY" in f08["attribution_epistemic_label"]

    aud = validate_r3_forensic_ledger_independently(gz_p, f07)
    assert aud["valid"] is True and aud["max_residual_usd"] <= 0.001

    # Tamper 1: drop losses from 07 -> verifier catches denominator mismatch
    f07_bad = copy.deepcopy(f07)
    f07_bad["overall_pooled_null_1x"]["losses"] = 0
    assert validate_r3_forensic_ledger_independently(gz_p, f07_bad)["valid"] is False

    # Tamper 2: zero out total fees in 07 -> verifier catches fee mismatch
    f07_fee = copy.deepcopy(f07)
    f07_fee["overall_pooled_null_1x"]["total_fees_usd"] = 0.0
    assert validate_r3_forensic_ledger_independently(gz_p, f07_fee)["valid"] is False


def test_negative_controls_direction_invert_future_mutation_and_conservative_tie(tmp_path):
    ds_sub, mtf, _led, r2c = _load_slice(3500)
    gz_p = tmp_path / "06_nc.jsonl.gz"
    _meta06, rows, _ = audit_r3_entry_exit_geometry_and_accounting(
        datasets=ds_sub, mtf_bundle=mtf, r2_cands_1x=r2c, out_ledger_gz_path=gz_p
    )
    f10 = run_r3_negative_control_and_label_integrity_tests(
        datasets=ds_sub, mtf_bundle=mtf, r2_cands_1x=r2c, forensic_rows=rows
    )
    assert f10["status"] == "PASS"
    assert f10["future_bar_mutation_invariance"]["mismatches_count"] == 0
    assert f10["synthetic_label_and_conservative_tie_controls"]["passed"] is True
    assert f10["direction_inverted_symmetric_sl_tp_control"]["inverted_closed"] > 0
