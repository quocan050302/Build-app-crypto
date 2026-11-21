"""
Dedicated KH-02.R2 unit tests: reference invariants, streaming causal unswept level ledger,
T01..T10 adversarial target causality, R1 counterexample audit, R2 pure target resolver,
paired E/G/X factorial design, and common-support SMD covariate balance.
"""
from __future__ import annotations

import copy
from pathlib import Path
import pytest

from services.kh_02_entry_foundation_research_service import KH02AsOfMTFRecord
from services.kh_02_r2_research_integrity_service import (
    ACTIVATION_READY_KH02_R2,
    CANONICAL_DATASET_SHA256_KH02_R2,
    RUNTIME_SAFETY_MODES_KH02_R2,
    _compute_smd,
    _make_synthetic_rec,
    audit_r2_function_ownership_and_keep_as_is,
    audit_r2_reference_inputs_and_invariants,
    build_r2_causal_unswept_level_ledger,
    build_r2_paired_factorial_entry_geometry_design,
    compute_r2_stratified_common_support_balance,
    freeze_r2_estimands_thresholds_and_falsification,
    resolve_r2_research_only_structural_target,
    run_r2_t01_to_t10_adversarial_target_scenarios,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def _make_synth_dataset(base_ts: int = 1_792_080_000_000, n_m1: int = 90) -> dict:
    def _gen(tf_ms: int, count: int) -> list:
        out = []
        st = base_ts - count * tf_ms
        for i in range(count):
            o_t = st + i * tf_ms
            wave = 3.0 if (i % 7 == 3) else (-2.5 if (i % 7 == 6) else 0.0)
            px = 4000.0 + 0.15 * i + wave
            out.append(
                {
                    "open_time": o_t,
                    "close_time": o_t + tf_ms,
                    "open": round(px, 2),
                    "high": round(px + 1.4, 2),
                    "low": round(px - 1.3, 2),
                    "close": round(px + 0.2, 2),
                    "volume": 25.0,
                    "is_closed": True,
                }
            )
        return out

    return {"1m": _gen(60_000, n_m1), "15m": _gen(900_000, 24), "1H": _gen(3_600_000, 24)}


def test_r2_reference_invariants_and_frozen_estimands() -> None:
    ref = audit_r2_reference_inputs_and_invariants(REPO_ROOT)
    assert ref["status"] == "PASS"
    assert ref["reused_dataset_provenance"]["sha256"] == CANONICAL_DATASET_SHA256_KH02_R2
    assert ref["reused_dataset_provenance"]["observed_l1_bbo_available"] is False
    assert ref["reused_dataset_provenance"]["actual_account_effective_fee_tier"] == "UNKNOWN_NOT_LOCALLY_VERIFIED"

    est = freeze_r2_estimands_thresholds_and_falsification(tested_executable_sha="a" * 40)
    assert est["frozen_before_outcome_reads"] is True
    assert est["ACTIVATION_READY"] == ACTIVATION_READY_KH02_R2
    assert est["runtime_safety_modes"] == RUNTIME_SAFETY_MODES_KH02_R2
    assert est["frozen_estimands"]["B0_VS_B2_CROSS_UNIVERSE"]["status"] == "COMPARISON_NOT_IDENTIFIABLE"


def test_r2_function_ownership_and_zero_locked_modifications() -> None:
    own = audit_r2_function_ownership_and_keep_as_is(REPO_ROOT, base_head_sha="HEAD")
    assert own["locked_files_modified_count"] == 0
    assert own["modified_existing_files_count"] == 0
    assert own["forbidden_order_import_hits"] == []
    assert own["cost_assumptions_defaults_intact"] is True
    assert own["status"] == "PASS"


def test_r2_t01_to_t10_adversarial_target_suite_all_pass() -> None:
    suite = run_r2_t01_to_t10_adversarial_target_scenarios()
    assert suite["scenario_count"] == 10
    assert suite["passed_count"] == 10
    assert suite["all_passed"] is True
    # Confirm R1 swept-target bug reproduced on synthetic T01 and T02
    assert suite["scenarios"][0]["r1_reproduced_swept_target_bug"] is True
    assert suite["scenarios"][1]["r1_reproduced_swept_target_bug"] is True


def test_r2_causal_ledger_rejects_bad_inputs_and_proves_t10_invariance() -> None:
    ds = _make_synth_dataset()
    led = build_r2_causal_unswept_level_ledger(ds, warmup_m1=15, run_t10_future_mutation_proof=True)
    assert led["t10_future_mutation_proof"]["executed"] is True
    assert led["t10_future_mutation_proof"]["bit_for_bit_identical"] is True
    assert led["total_confirmed_levels"] > 0

    # Unclosed candle rejected
    ds_unclosed = copy.deepcopy(ds)
    ds_unclosed["15m"][-1]["is_closed"] = False
    with pytest.raises(ValueError, match="UNCLOSED_CANDLE_FORBIDDEN"):
        build_r2_causal_unswept_level_ledger(ds_unclosed, run_t10_future_mutation_proof=False)

    # Duplicate close_time rejected
    ds_dup = copy.deepcopy(ds)
    ds_dup["1m"][10]["close_time"] = ds_dup["1m"][9]["close_time"]
    with pytest.raises(ValueError, match="DUPLICATE_OR_INVALID_CLOSE_TIME"):
        build_r2_causal_unswept_level_ledger(ds_dup, run_t10_future_mutation_proof=False)

    # Negative latency rejected
    with pytest.raises(ValueError, match="NEGATIVE_SOURCE_LATENCY_FORBIDDEN"):
        build_r2_causal_unswept_level_ledger(ds, source_latency_ms=-5, run_t10_future_mutation_proof=False)

    # Hidden future key rejected
    ds_fut = copy.deepcopy(ds)
    ds_fut["1H"][5]["future_high"] = 4999.0
    with pytest.raises(ValueError, match="FORBIDDEN_FUTURE_KEY_IN_BAR"):
        build_r2_causal_unswept_level_ledger(ds_fut, run_t10_future_mutation_proof=False)


def test_r2_resolver_rejects_invalid_enum_missing_atr_and_enforces_latency_and_tiebreak() -> None:
    dec_ts = 1_792_080_000_000
    rec = _make_synthetic_rec(decision_ts_ms=dec_ts)

    with pytest.raises(ValueError, match="INVALID_DIRECTION"):
        resolve_r2_research_only_structural_target(rec, "FLAT", unswept_highs_asof=[], unswept_lows_asof=[])

    rec_no_atr = KH02AsOfMTFRecord(**{**rec.__dict__, "m1_atr_14": None})
    r_no_atr = resolve_r2_research_only_structural_target(rec_no_atr, "LONG", unswept_highs_asof=[], unswept_lows_asof=[])
    assert r_no_atr["accepted"] is False
    assert r_no_atr["rejection_reason"] == "MISSING_CAUSAL_M1_ATR"

    # Exact latency boundary accepted; +1ms rejected
    lvl = {"level_id": "15m_L1", "source_tf": "15m", "price": 4008.0, "confirmation_time_ms": dec_ts - 2000, "first_swept_ts_ms": None}
    ok = resolve_r2_research_only_structural_target(rec, "LONG", unswept_highs_asof=[lvl], unswept_lows_asof=[], source_latency_ms=2000)
    late = resolve_r2_research_only_structural_target(rec, "LONG", unswept_highs_asof=[lvl], unswept_lows_asof=[], source_latency_ms=2001)
    assert ok["accepted"] is True
    assert late["accepted"] is False

    # Order-invariant tie-break
    l_h1 = {"level_id": "1H_T", "source_tf": "1H", "price": 4008.0, "confirmation_time_ms": dec_ts - 7_200_000, "first_swept_ts_ms": None}
    l_m15 = {"level_id": "15m_T", "source_tf": "15m", "price": 4008.0, "confirmation_time_ms": dec_ts - 1_800_000, "first_swept_ts_ms": None}
    r_a = resolve_r2_research_only_structural_target(rec, "LONG", unswept_highs_asof=[l_h1, l_m15], unswept_lows_asof=[])
    r_b = resolve_r2_research_only_structural_target(rec, "LONG", unswept_highs_asof=[l_m15, l_h1], unswept_lows_asof=[])
    assert r_a["target_level_id"] == r_b["target_level_id"] == "15m_T"


def test_r2_paired_design_reconciles_unmatched_and_flags_matching_inadequate() -> None:
    def _mk_cand(oid: str, ts: int, tp: float, d: str = "LONG") -> dict:
        return {
            "opportunity_id": oid,
            "direction": d,
            "checkpoint_index_1m": 40,
            "decision_timestamp_ms": ts,
            "selected_sl": 3998.0,
            "take_profit": tp,
            "target_level_id": f"lvl_{tp}",
            "ny_session_bucket": "NY_AM",
            "h1_atr_quantile": 0.7,
            "m1_atr_14": 0.9,
        }

    def _mk_ep(oid: str, ts: int, tp: float, net_r: float, d: str = "LONG", sess: str = "NY_AM") -> dict:
        return {
            "opportunity_id": oid,
            "setup_episode_id": f"ep_{oid}",
            "direction": d,
            "checkpoint_index_1m": 40,
            "decision_timestamp_ms": ts,
            "take_profit": tp,
            "filled": True,
            "outcome_label": "WIN" if net_r > 0 else "LOSS",
            "realized_net_r": net_r,
            "realized_net_pnl_usd": round(net_r * 2.5, 4),
            "realized_gross_pnl_usd": round(net_r * 2.5 + 0.1, 4),
            "total_fees_usd": 0.1,
            "ny_date": f"2026-10-{10 + (ts % 5)}",
            "ny_session_bucket": sess,
        }

    r1_c = {"arm_candidates": {"B2_H1": [_mk_cand("o1", 1000, 4006.0), _mk_cand("o2", 2000, 4007.0)], "NC_NULL_TRIGGER": [_mk_cand("o1", 1000, 4006.0), _mk_cand("o2", 2000, 4007.0)]}}
    r2_c = {
        "total_directional_bar_opportunities": 100,
        "raw_r2_geometry_risk_eligible_count": 3,
        "arm_candidates": {
            "B2_H1": [_mk_cand("o1", 1000, 4008.0), _mk_cand("o3", 3000, 4009.0)],
            "NC_NULL_TRIGGER": [_mk_cand("o1", 1000, 4008.0), _mk_cand("o2", 2000, 4007.0), _mk_cand("o3", 3000, 4009.0)],
        },
    }
    r1_rep = {
        "B2_H1": {"episodes_by_mode": {"NEXT_BAR_OPEN_BID_ASK": [_mk_ep("o1", 1000, 4006.0, -1.0), _mk_ep("o2", 2000, 4007.0, -1.0)]}},
        "NC_NULL_TRIGGER": {"episodes_by_mode": {"NEXT_BAR_OPEN_BID_ASK": [_mk_ep("o1", 1000, 4006.0, -1.0), _mk_ep("o2", 2000, 4007.0, 2.0)]}},
    }
    r2_rep = {
        "B2_H1": {"episodes_by_mode": {"NEXT_BAR_OPEN_BID_ASK": [_mk_ep("o1", 1000, 4008.0, -1.0), _mk_ep("o3", 3000, 4009.0, 2.0)]}},
        "NC_NULL_TRIGGER": {"episodes_by_mode": {"NEXT_BAR_OPEN_BID_ASK": [_mk_ep("o1", 1000, 4008.0, -1.0), _mk_ep("o2", 2000, 4007.0, -1.0), _mk_ep("o3", 3000, 4009.0, 2.0)]}},
    }

    pd = build_r2_paired_factorial_entry_geometry_design(
        r1_candidates=r1_c, r2_candidates=r2_c, r1_replays=r1_rep, r2_replays=r2_rep
    )
    assert pd["zero_silent_drops_verified"] is True
    assert pd["B0_VS_B2_CROSS_UNIVERSE"]["identifiability_status"] == "COMPARISON_NOT_IDENTIFIABLE"
    g_h1 = pd["G_ONLY_PAIRED_SAME_ENTRY"]["B2_H1"]
    assert g_h1["matched_exact_trades_count"] == 1
    assert g_h1["unmatched_r1_only_trades"] == 1
    assert g_h1["unmatched_r2_only_trades"] == 1
    assert g_h1["population_classification"] == "FULL_RERISKED_DIFFERENT_POPULATION"

    # Tiny sample -> MUST report MATCHING_INADEQUATE (never false causal lift!)
    bal = compute_r2_stratified_common_support_balance(
        r2_candidates=r2_c, r2_replays=r2_rep, mtf_bundle={"records": []}
    )
    assert bal["arm_balance_reports"]["B2_H1"]["matching_quality_status"] == "MATCHING_INADEQUATE"
    assert bal["arm_balance_reports"]["B2_H1"]["causal_lift_claim_permitted"] is False
    assert _compute_smd([1.0, 1.0, 1.0], [0.0, 0.0, 0.0]) is None or abs(_compute_smd([1.0, 1.0, 0.0], [0.0, 0.0, 1.0]) or 0.0) > 0.10
