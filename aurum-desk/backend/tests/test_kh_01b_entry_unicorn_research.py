"""
backend/tests/test_kh_01b_entry_unicorn_research.py

Unit, contract, and ontology-isolation tests for KH-01B (`F01`–`F09`, `F13`, `B00`–`B12`).
"""
from __future__ import annotations

from pathlib import Path
import pytest

from services.kh_01b_entry_unicorn_research_service import (
    CANONICAL_DATASET_SHA256_KH01B,
    DEFAULT_KH01B_VARIANT_SPECS,
    SPEC_VERSION_KH01B,
    _build_synthetic_strict_unicorn_fixture,
    audit_kh01b_true_unicorn_positive_and_negative_fixtures,
    build_kh01b_callgraph_and_keep_as_is_matrix,
    evaluate_htf_alignment_hypothesis,
    evaluate_macro_window_hypothesis,
    evaluate_source_tf_target_reachability_hypothesis,
    evaluate_strict_true_unicorn_hypothesis,
    evaluate_sweep_mss_displacement_hypothesis,
    evaluate_unicorn_adjacent_research_hypothesis,
    extract_kh01b_causal_feature_frame,
    load_and_verify_kh01b_reference_baseline,
    pre_register_kh01b_hypotheses,
)
from tools.run_v17_9_3_macro_unicorn_backtest import (
    V1793_VARIANT_POLICIES,
    build_v1793_variant_market_context,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_kh01b_f01_to_f12_contracts_and_preregistration() -> None:
    # F01: Reference baseline verification on canonical repo
    truth = load_and_verify_kh01b_reference_baseline(REPO_ROOT)
    assert truth.verification_passed is True
    assert truth.prior_use_status == "REUSED_RESEARCH"
    assert truth.dataset_sha256 == CANONICAL_DATASET_SHA256_KH01B
    assert truth.a1_candidate_count == 56710
    assert truth.si8_discovery_count == 6375
    assert truth.total_candidate_universe_count == 63085
    assert truth.si0_reference_scheduled == 242
    assert truth.si0_reference_closed == 166
    assert truth.si0_reference_wins == 9
    assert truth.si0_reference_losses == 157

    # Fail-closed when reference pointer is missing
    bad_truth = load_and_verify_kh01b_reference_baseline(
        REPO_ROOT, {"r1_latest_ptr_rel": "aurum-desk/docs/artifacts/kh_01a_r1/nonexistent_ptr.json"}
    )
    assert bad_truth.verification_passed is False
    assert any("MISSING_R1_LATEST_POINTER" in v for v in bad_truth.violations)

    # F02: Pre-registration freezes B00..B12 and tracks amendments via distinct hash
    m1 = pre_register_kh01b_hypotheses(CANONICAL_DATASET_SHA256_KH01B, "cfg_sha_1", "code_sha_1")
    assert m1.spec_version == SPEC_VERSION_KH01B
    assert m1.registered_variants_count == 13
    assert m1.frozen_before_backtest_execution is True

    m1_amended = pre_register_kh01b_hypotheses(
        CANONICAL_DATASET_SHA256_KH01B, "cfg_sha_1", "code_sha_1",
        amendments=[{"amendment_id": "AM-01", "reason": "Explicit documented test amendment"}],
    )
    assert m1_amended.manifest_sha256 != m1.manifest_sha256
    assert "AMENDED" in m1_amended.manifest_id

    # Reject adjacent ontology trying to count toward D4
    bad_specs = [dict(x) for x in DEFAULT_KH01B_VARIANT_SPECS]
    bad_specs[10] = {**bad_specs[10], "rule": {"type": "UNICORN_ADJACENT", "counts_toward_d4": True}}
    with pytest.raises(ValueError, match="ONTOLOGY_LEAKAGE_ADJACENT_MARKED_D4"):
        pre_register_kh01b_hypotheses(CANONICAL_DATASET_SHA256_KH01B, "cfg_sha_1", "code_sha_1", bad_specs)

    # Callgraph & Keep-As-Is AST matrix check
    cg = build_kh01b_callgraph_and_keep_as_is_matrix(REPO_ROOT, baseline_sha="a" * 40, tested_sha="b" * 40)
    assert cg["existing_backend_services_modified_count"] == 0
    assert cg["macro_call_site_ast_audit"]["invalid_production_call_sites_count"] == 0
    assert len(cg["new_kh01b_contract_functions"]) == 16


def test_kh01b_strict_unicorn_vs_adjacent_ontology_isolation() -> None:
    ts_ms, m1_long, htf_long = _build_synthetic_strict_unicorn_fixture("LONG")
    pol_d2 = dict(V1793_VARIANT_POLICIES["D2_V1793_MACRO_PLUS_UNICORN_MTF_FULL"])
    ctx_long = build_v1793_variant_market_context(
        {
            "symbol": "XAUUSDT",
            "decision_timestamp_ms": ts_ms,
            "m1_candles": m1_long,
            "m5_candles": htf_long["5m"],
            "m15_candles": htf_long["15m"],
            "h1_candles": htf_long["1H"],
            "h4_candles": htf_long["4H"],
            "d1_candles": htf_long["1D"],
            "bid": 3007.40,
            "ask": 3007.60,
        },
        pol_d2,
        decision_time=ts_ms,
    )
    cand = {
        "opportunity_id": "opp_iso_1",
        "setup_episode_id": "ep_iso_1",
        "model_id": "SMC_M1_FVG_CONTINUATION",
        "direction": "LONG",
        "decision_timestamp_ms": ts_ms,
        "checkpoint_index_1m": 30,
        "entry_reference": 3007.50,
        "selected_sl": 3003.50,
        "take_profit": 3017.50,
        "hypothetical_quantity": 0.50,
        "planned_net_risk_usd": 2.20,
        "net_rr": 2.25,
        "gross_rr": 2.50,
    }
    # Positive live context passes strict D4 True Unicorn
    fr_pos = extract_kh01b_causal_feature_frame(
        cand,
        {"opp_iso_1": {"opportunity_id": "opp_iso_1", "decision_timestamp_ms": ts_ms, "h4_bias": "BULLISH", "h1_bias": "BULLISH"}},
        {},
        ts_ms,
    )
    strict_pos = evaluate_strict_true_unicorn_hypothesis(fr_pos, context=ctx_long)
    assert strict_pos.is_valid_strict_unicorn is True
    assert strict_pos.ontology == "STRICT_D4_TRUE_UNICORN"

    # FVG-only without Breaker Block MUST fail strict D4 but MAY pass Adjacent B10 with counts_toward_d4=False
    fr_adj = extract_kh01b_causal_feature_frame(
        cand,
        {
            "opp_iso_1": {
                "opportunity_id": "opp_iso_1",
                "decision_timestamp_ms": ts_ms,
                "h4_bias": "BULLISH",
                "h1_bias": "BULLISH",
                "sweep_confirmed": True,
                "sweep_reclaimed": True,
                "mss_confirmed": True,
                "displacement_valid": True,
                "fvg_present": True,
                "fvg_unmitigated": True,
                "fvg_retest_valid": True,
                "unicorn_breaker_fvg_bound": False,
                "unicorn_classification": "FVG_ONLY_NO_BREAKER",
            }
        },
        {},
        ts_ms,
    )
    d4_neg = evaluate_strict_true_unicorn_hypothesis(fr_adj)
    adj_pos = evaluate_unicorn_adjacent_research_hypothesis(fr_adj)
    assert d4_neg.is_valid_strict_unicorn is False
    assert "05_NOT_TRUE_UNICORN_BREAKER_FVG" in str(d4_neg.terminal_rejection_code)
    assert adj_pos.eligible is True
    assert adj_pos.ontology == "ADJACENT_FVG_SWEEP_MSS"
    assert adj_pos.counts_toward_d4_true_unicorn is False

    # Adjacent evaluator refuses True-Unicorn / D4 variant naming
    with pytest.raises(ValueError, match="ONTOLOGY_VIOLATION_ADJACENT_CANNOT_USE_TRUE_UNICORN_ID"):
        evaluate_unicorn_adjacent_research_hypothesis(fr_adj, variant_id="KH01B-D4-TRUE-UNICORN")


def test_kh01b_b04_filter_vs_b11_tp_counterfactual_boundary() -> None:
    ts_ms, m1_long, htf_long = _build_synthetic_strict_unicorn_fixture("LONG")
    datasets = {"1m": m1_long, "5m": htf_long["5m"], "15m": htf_long["15m"], "1H": htf_long["1H"], "4H": htf_long["4H"]}
    close_ts = {k: [b["close_time"] for b in v] for k, v in datasets.items()}
    pools = {
        "pool_5m_far": {"pool_id": "pool_5m_far", "timeframe": "5M", "price": 3080.0, "known_at_ms": ts_ms - 600_000},
        "pool_15m_near": {"pool_id": "pool_15m_near", "timeframe": "15M", "price": 3026.5, "known_at_ms": ts_ms - 900_000},
    }
    cand = {
        "opportunity_id": "opp_tp_1",
        "setup_episode_id": "ep_tp_1",
        "model_id": "SMC_M1_CONTINUATION",
        "direction": "LONG",
        "decision_timestamp_ms": ts_ms,
        "checkpoint_index_1m": 30,
        "entry_reference": 3007.50,
        "selected_sl": 3003.50,
        "take_profit": 3080.00,  # Far beyond 12 x 5M ATR!
        "selected_target_id": "pool_5m_far",
        "hypothetical_quantity": 0.50,
        "planned_net_risk_usd": 2.20,
        "net_rr": 15.0,
    }
    fr = extract_kh01b_causal_feature_frame(
        cand,
        {"opp_tp_1": {"opportunity_id": "opp_tp_1", "decision_timestamp_ms": ts_ms, "target_pool_id": "pool_5m_far", "target_unswept": True}},
        {},
        ts_ms,
        pools_by_id=pools,
        datasets=datasets,
        close_ts_index=close_ts,
    )
    b04_res = evaluate_source_tf_target_reachability_hypothesis(fr, horizon=12.0, allow_tp_reselection_counterfactual=False)
    assert b04_res.eligible is False
    assert b04_res.tp_modified is False
    assert b04_res.selected_tp == 3080.00

    b11_res = evaluate_source_tf_target_reachability_hypothesis(
        fr,
        horizon=12.0,
        allow_tp_reselection_counterfactual=True,
        causal_pools_at_ts=list(pools.values()),
        atr_by_tf_at_ts={"5M": 2.5, "15M": 3.5},
    )
    assert b11_res.eligible is True
    assert b11_res.mode == "TP_ONLY_RESEARCH_B11"
    assert b11_res.tp_modified is True
    assert b11_res.selected_tp == 3026.50
    assert b11_res.recomputed_net_rr is not None and b11_res.recomputed_net_rr >= 2.0
    assert b11_res.recomputed_planned_risk_usd is not None and b11_res.recomputed_planned_risk_usd <= 2.50


def test_kh01b_f13_strict_unicorn_positive_and_negative_fixtures() -> None:
    val = audit_kh01b_true_unicorn_positive_and_negative_fixtures()
    assert val.status == "PASS"
    assert val.total_cases == 25
    assert val.passed_cases == 25
    assert val.failed_cases == 0
    assert val.synthetic_counted_as_historical_trades == 0
