"""
backend/tests/test_kh_01b_r1_causal_and_forensics.py

Causal anti-leakage, trailing-only quantile invariance, original-vs-reconstructed PARTIAL
honesty, AST keep-as-is callgraph, frozen <=6 preregistration budget, and 24-mutation
RED/GREEN adversarial verification for KH-01B.R1.
"""
from __future__ import annotations

import copy
from pathlib import Path
import pytest

from services.kh_01b_r1_entry_edge_discovery_service import (
    CANONICAL_DATASET_SHA256_KH01B_R1,
    DEFAULT_KH01B_R1_THESIS_SPECS,
    SPEC_VERSION_KH01B_R1,
    build_kh01b_r1_function_callgraph_and_keep_as_is_map,
    evaluate_kh01b_r1_entry_variant,
    extract_kh01b_r1_causal_regime_features,
    load_and_verify_kh01b_r1_sources,
    pre_register_kh01b_r1_theses,
    run_kh01b_r1_real_fault_injections,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def _make_closed_tf_series(end_ts_ms: int, tf_ms: int, count: int, slope: float = -0.35) -> list[dict]:
    aligned_end = (end_ts_ms // tf_ms) * tf_ms
    start_open = aligned_end - count * tf_ms
    bars = []
    for i in range(count):
        o_ms = start_open + i * tf_ms
        px = 3100.0 + i * slope
        bars.append({
            "timestamp": o_ms,
            "open_time": o_ms,
            "close_time": o_ms + tf_ms,
            "open": round(px - slope * 0.4, 2),
            "high": round(px + 0.9 + (i % 7) * 0.25, 2),
            "low": round(px - 0.8, 2),
            "close": round(px, 2),
            "volume": 150.0,
            "is_closed": True,
        })
    return bars


def test_kh01b_r1_sources_callgraph_and_preregistration_budget() -> None:
    src = load_and_verify_kh01b_r1_sources(REPO_ROOT, "fe19e6b5b4b0")
    assert src.verification_passed is True
    assert src.canonical_dataset_sha256 == CANONICAL_DATASET_SHA256_KH01B_R1
    assert src.a1_candidate_count == 56710
    assert src.si8_discovery_count == 6375
    assert src.total_candidate_universe_count == 63085
    assert src.si0_scheduled_count == 242
    assert src.si0_closed_count == 166
    assert src.si0_wins_count == 9
    assert src.si0_losses_count == 157
    assert src.forensics_170_partial_count == 170
    assert src.prior_kh01b_b09_closed_trades == 0

    bad_src = load_and_verify_kh01b_r1_sources(REPO_ROOT, "fe19e6b5b4b0", branch_override="main")
    assert bad_src.verification_passed is False
    assert any("WRONG_MANDATORY_BRANCH" in v for v in bad_src.violations)

    cg = build_kh01b_r1_function_callgraph_and_keep_as_is_map(
        REPO_ROOT, starting_head_sha="a" * 40, tested_sha="b" * 40
    )
    assert cg["existing_production_or_prior_kh_files_modified_count"] == 0
    assert cg["macro_call_site_ast_audit"]["invalid_production_call_sites_count"] == 0
    assert len(cg["keep_as_is_functions"]) >= 15
    assert len(cg["new_kh01b_r1_functions"]) == 13

    # Pre-registration enforces <= 6 new hypotheses + E00 control
    m_full = pre_register_kh01b_r1_theses(CANONICAL_DATASET_SHA256_KH01B_R1, "cfg1", "code1", e03_completeness_ratio=0.99)
    assert m_full.spec_version == SPEC_VERSION_KH01B_R1
    assert m_full.registered_new_hypotheses_count == 6
    assert m_full.total_arms_including_control == 7
    assert m_full.e06_execution_status == "EXECUTABLE_PRECONDITIONS_MET"
    assert m_full.frozen_before_outcome_evaluation is True

    # E06 automatically marked NOT_RUN_JUSTIFIED when E03 completeness < 0.95 before outcomes
    m_low_comp = pre_register_kh01b_r1_theses(CANONICAL_DATASET_SHA256_KH01B_R1, "cfg1", "code1", e03_completeness_ratio=0.80)
    assert m_low_comp.e06_execution_status == "NOT_RUN_JUSTIFIED"

    # Amendments produce distinct manifest_id and SHA256
    m_am = pre_register_kh01b_r1_theses(
        CANONICAL_DATASET_SHA256_KH01B_R1, "cfg1", "code1",
        amendments=[{"id": "AM1", "reason": "documented amendment"}],
    )
    assert m_am.manifest_sha256 != m_full.manifest_sha256
    assert "AMENDED" in m_am.manifest_id

    # Over-budget 7th new hypothesis rejected
    over = list(DEFAULT_KH01B_R1_THESIS_SPECS) + [{
        "short_code": "E07", "variant_id": "KH01B_R1-E07-OVER", "is_baseline_control": False,
        "ontology": "OVER", "modifies_tp_geometry": False, "rule": {"type": "DIRECTION_ONLY", "allowed_direction": "EXTRA"},
    }]
    with pytest.raises(ValueError, match="HYPOTHESIS_BUDGET_EXCEEDED"):
        pre_register_kh01b_r1_theses(CANONICAL_DATASET_SHA256_KH01B_R1, "cfg1", "code1", over)


def test_kh01b_r1_trailing_quantile_invariance_under_future_bar_shock() -> None:
    ts_ms = 1_792_003_600_000
    b1 = _make_closed_tf_series(ts_ms, 60_000, 90, slope=-0.10)
    b15 = _make_closed_tf_series(ts_ms, 900_000, 24, slope=-0.60)
    bh1 = _make_closed_tf_series(ts_ms, 3_600_000, 72, slope=-1.20)

    ds_base = {"1m": b1, "15m": b15, "1H": bh1}
    cts_base = {k: [int(b["close_time"]) for b in v] for k, v in ds_base.items()}
    cbi_base = {(tf_u, int(b["open_time"])): int(b["close_time"]) for tf_u, k in (("1M", "1m"), ("15M", "15m"), ("1H", "1H")) for b in ds_base[k]}

    # Append massive future volatility spikes strictly AFTER ts_ms across M1, M15, and H1
    fut_1m = [{"timestamp": ts_ms + k * 60_000, "open_time": ts_ms + k * 60_000, "close_time": ts_ms + (k + 1) * 60_000, "open": 3000.0, "high": 5000.0, "low": 1500.0, "close": 4800.0, "volume": 9999.0, "is_closed": True} for k in range(20)]
    fut_15m = [{"timestamp": ts_ms + k * 900_000, "open_time": ts_ms + k * 900_000, "close_time": ts_ms + (k + 1) * 900_000, "open": 3000.0, "high": 5000.0, "low": 1500.0, "close": 4800.0, "volume": 9999.0, "is_closed": True} for k in range(10)]
    fut_1h = [{"timestamp": ts_ms + k * 3_600_000, "open_time": ts_ms + k * 3_600_000, "close_time": ts_ms + (k + 1) * 3_600_000, "open": 3000.0, "high": 5000.0, "low": 1500.0, "close": 4800.0, "volume": 9999.0, "is_closed": True} for k in range(10)]

    ds_pert = {"1m": b1 + fut_1m, "15m": b15 + fut_15m, "1H": bh1 + fut_1h}
    cts_pert = {k: [int(b["close_time"]) for b in v] for k, v in ds_pert.items()}
    cbi_pert = {(tf_u, int(b["open_time"])): int(b["close_time"]) for tf_u, k in (("1M", "1m"), ("15M", "15m"), ("1H", "1H")) for b in ds_pert[k]}

    cand = {
        "opportunity_id": "opp_inv_1",
        "setup_episode_id": "ep_inv_1",
        "model_id": "SMC_M1_FVG_CONTINUATION",
        "direction": "SHORT",
        "decision_timestamp_ms": ts_ms,
        "checkpoint_index_1m": len(b1) - 1,
        "decision_status": "ELIGIBLE",
        "entry_reference": 3010.0,
        "selected_sl": 3014.0,
        "take_profit": 3000.0,
        "hypothetical_quantity": 0.50,
        "planned_net_risk_usd": 2.25,
        "net_rr": 2.20,
        "trade_action_fingerprint": "fp_1",
        "decision_fingerprint": "dfp_1",
    }

    fr1 = extract_kh01b_r1_causal_regime_features(cand, {}, cbi_base, ts_ms, datasets=ds_base, close_ts_index=cts_base)
    fr2 = extract_kh01b_r1_causal_regime_features(cand, {}, cbi_pert, ts_ms, datasets=ds_pert, close_ts_index=cts_pert)

    assert fr1.r1_frame_sha256 == fr2.r1_frame_sha256
    assert fr1.predictive_pre_entry["h1_atr_trailing_48_percentile"] == fr2.predictive_pre_entry["h1_atr_trailing_48_percentile"]
    assert fr1.predictive_pre_entry["m15_signed_dir_efficiency_8"] == fr2.predictive_pre_entry["m15_signed_dir_efficiency_8"]

    # Every same-timestamp pre-entry arm E00..E04, E06 must make the exact same decision
    for sp in DEFAULT_KH01B_R1_THESIS_SPECS:
        if sp["short_code"] == "E05":
            continue
        d1 = evaluate_kh01b_r1_entry_variant(sp["rule"], sp["short_code"], sp["variant_id"], cand, fr1)
        d2 = evaluate_kh01b_r1_entry_variant(sp["rule"], sp["short_code"], sp["variant_id"], cand, fr2)
        assert (d1.eligible, d1.tri_state, d1.rejection_code) == (d2.eligible, d2.tri_state, d2.rejection_code)


def test_kh01b_r1_all_24_real_fault_injections_pass() -> None:
    res = run_kh01b_r1_real_fault_injections(REPO_ROOT)
    assert res["status"] == "PASS"
    assert res["total_mutations"] == 24
    assert res["passed_mutations"] == 24
    assert res["failed_mutations"] == 0
    assert res["synthetic_fixtures_counted_in_historical_pnl"] == 0
    for mut in res["mutations"]:
        assert mut["red_rejected"] is True and mut["green_accepted"] is True, f"Mutation failed: {mut}"
