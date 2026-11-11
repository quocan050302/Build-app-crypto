from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, List

import pytest

from services.v17_9_2_structural_stop_intelligence import (
    TRUE_DELAYED_ENTRY_VARIANTS_V17931213,
    evaluate_r3_counterfactual_stop_and_target_v17931212,
    evaluate_shared_delayed_entry_adapter_v17931213,
)
from services.v17_9_3_1_2_1_root_cause_evidence_service import (
    build_full_intervention_candidate_index_v1793121,
    emit_causal_order_intents_v1793121,
)
from services.v17_9_3_1_2_7_causal_signal_truth import (
    rebuild_materialized_intent_proof_v1793127,
)
from services.v17_9_3_1_2_10_canonical_reconciliation_and_provenance import (
    build_si8_si9_278_materialization_diagnosis_v17931210,
)
from services.v17_9_3_1_2_12_provenance_forensics_and_repair_service import (
    verify_r2_causal_mtf_unicorn_provenance_v17931212,
)
from services.v17_9_3_1_2_13_delayed_entry_materialization_and_truth_service import (
    ALL_MATCHED_AND_DELAYED_VARIANTS_V17931213,
    AuthoritativeMetricsV17931213,
    CANONICAL_DATASET_SHA256_V17931213,
    MANDATORY_BRANCH_V17931213,
    VERSION_V17931213,
    _replay_chronological_1pos_portfolio_from_matched_rows_v17931213,
    build_v17931213_callgraph_and_defect_matrix,
    evaluate_v17931213_gate_matrix,
    render_v17931213_readme_and_handover,
    run_negative_controls_v01_to_v66_v17931213,
    verify_delayed_entry_and_structural_sl_tp_v17931213,
    verify_v17931213_authoritative_report_and_gates_consistency,
)
from services.v17_9_3_1_2_executable_intervention_replay_service import (
    ExecutableResearchPolicyV179312,
    _check_causal_htf_alignment_at_ts_v179312,
    build_close_ts_index_v179312,
    find_next_causal_entry_confirmation_v179312,
)


def _build_synthetic_bullish_mtf_dataset() -> Dict[str, List[Dict[str, Any]]]:
    """Builds strictly closed M1/M5/M15/1H/4H candles with clear bullish swings, confirmation & unswept highs."""
    bars_4h: List[Dict[str, Any]] = []
    # Multi-bar wave higher highs & higher lows (left_bars=2, right_bars=2) so evaluate_htf_structure -> BULLISH
    base_ms = 1_700_000_000_000
    pts = [
        4000, 4005, 4012, 4020, 4015, 4010,
        4016, 4024, 4032, 4026, 4020,
        4028, 4036, 4045, 4039, 4033,
        4042, 4052, 4062, 4055, 4048,
        4058, 4068, 4078, 4072, 4066,
        4075, 4082, 4085,
    ]
    for i, px in enumerate(pts):
        o_ms = base_ms + i * 14_400_000
        bars_4h.append(
            {
                "open_time": o_ms,
                "close_time": o_ms + 14_400_000,
                "open": float(px - 3.0),
                "high": float(px + 6.0),
                "low": float(px - 6.0),
                "close": float(px + 2.0),
                "is_closed": True,
            }
        )

    end_4h_ms = bars_4h[-1]["close_time"]
    bars_1h: List[Dict[str, Any]] = []
    for i, px in enumerate(pts):
        o_ms = end_4h_ms - (len(pts) - i) * 3_600_000
        bars_1h.append(
            {
                "open_time": o_ms,
                "close_time": o_ms + 3_600_000,
                "open": float(px - 2.0),
                "high": float(px + 5.0),
                "low": float(px - 5.0),
                "close": float(px + 2.0),
                "is_closed": True,
            }
        )

    pts_ltf = [
        4070.0, 4071.5, 4073.0, 4075.0, 4074.0, 4073.0,
        4074.5, 4076.0, 4078.0, 4076.8, 4075.8,
        4077.2, 4078.8, 4080.5, 4079.4, 4078.4,
        4079.8, 4081.2, 4083.0, 4082.0, 4081.0,
        4082.4, 4083.8, 4085.2, 4084.2, 4083.2,
        4084.2, 4084.8, 4085.2,
    ]
    bars_15m: List[Dict[str, Any]] = []
    for i, px in enumerate(pts_ltf):
        o_ms = end_4h_ms - (len(pts_ltf) - i) * 900_000
        bars_15m.append(
            {
                "open_time": o_ms,
                "close_time": o_ms + 900_000,
                "open": round(float(px - 0.3), 2),
                "high": round(float(px + 0.6), 2),
                "low": round(float(px - 0.6), 2),
                "close": round(float(px + 0.3), 2),
                "is_closed": True,
            }
        )

    bars_5m: List[Dict[str, Any]] = []
    for i, px in enumerate(pts_ltf):
        o_ms = end_4h_ms - (len(pts_ltf) - i) * 300_000
        bars_5m.append(
            {
                "open_time": o_ms,
                "close_time": o_ms + 300_000,
                "open": round(float(px - 0.2), 2),
                "high": round(float(px + 0.5), 2),
                "low": round(float(px - 0.5), 2),
                "close": round(float(px + 0.2), 2),
                "is_closed": True,
            }
        )

    # M1 bars around end_4h_ms:
    # - Create an earlier unswept swing high at 4122.0 (index 10) for TP target pool (~2.38x Net RR after Bitget 6bpsx2 fees)
    # - Create a protected swing low at 4081.8 (index 30)
    # - Base decision at index 40 (close_time = end_4h_ms, close = 4085.2)
    # - Bar 41..42 mild pullback, Bar 43 strong bullish confirmation close at 4087.4 (wait_bars = 3)
    # - Bars 44..79 rally cleanly to > 4130.0 hitting TP!
    m1_start = end_4h_ms - 41 * 60_000
    bars_1m: List[Dict[str, Any]] = []
    for i in range(80):
        o_ms = m1_start + i * 60_000
        if i == 10:
            o, h, l, c = 4118.0, 4122.0, 4116.0, 4117.0
        elif i in (8, 9, 11, 12):
            o, h, l, c = 4110.0, 4114.0, 4108.0, 4109.0
        elif i == 30:
            o, h, l, c = 4083.0, 4083.5, 4081.8, 4083.2
        elif i in (28, 29, 31, 32):
            o, h, l, c = 4084.0, 4085.0, 4083.2, 4084.6
        elif i == 40:
            # Base signal bar (strong bullish too, to prove k == base_idx is NEVER used when strictly_after_base=True!)
            o, h, l, c = 4082.0, 4085.5, 4081.8, 4085.2
        elif i in (41, 42):
            # Mild consolidation / non-confirming bars
            o, h, l, c = 4085.0, 4085.4, 4084.5, 4084.8
        elif i == 43:
            # Post-signal confirmation bar (k = 43 -> wait_bars = 3, body >= 1.25 * ATR_1m)
            o, h, l, c = 4084.6, 4087.6, 4084.5, 4087.4
        elif i > 43:
            step = (i - 43) * 1.2
            o, h, l, c = 4087.4 + step - 0.5, 4087.4 + step + 1.5, 4087.4 + step - 0.8, 4087.4 + step + 1.0
        else:
            o, h, l, c = 4084.6, 4085.1, 4084.2, 4084.8
        bars_1m.append(
            {
                "open_time": o_ms,
                "close_time": o_ms + 60_000,
                "open": round(o, 2),
                "high": round(h, 2),
                "low": round(l, 2),
                "close": round(c, 2),
                "is_closed": True,
            }
        )

    bars_1d: List[Dict[str, Any]] = []
    for i, px in enumerate(pts):
        o_ms = end_4h_ms - (len(pts) - i) * 86_400_000
        bars_1d.append(
            {
                "open_time": o_ms,
                "close_time": o_ms + 86_400_000,
                "open": float(px - 4.0),
                "high": float(px + 8.0),
                "low": float(px - 8.0),
                "close": float(px + 3.0),
                "is_closed": True,
            }
        )

    return {"1m": bars_1m, "5m": bars_5m, "15m": bars_15m, "1H": bars_1h, "4H": bars_4h, "1D": bars_1d}


def test_p0_e1_strict_post_signal_confirmation_and_true_delayed_variants() -> None:
    ds = _build_synthetic_bullish_mtf_dataset()
    bars_1m = ds["1m"]
    base_idx = 40
    base_ts = int(bars_1m[base_idx]["close_time"])

    # Even though bar 40 is bullish, strictly_after_base=True must scan k >= 41 and pick k = 43 (wait_bars = 3)
    conf = find_next_causal_entry_confirmation_v179312(
        {
            "checkpoint_index_1m": base_idx,
            "decision_timestamp_ms": base_ts,
            "direction": "LONG",
            "model_id": "SMC_M1_CONTINUATION",
            "selected_sl": 4080.0,
            "structural_invalidation_price": 4078.0,
        },
        bars_1m,
        max_wait_bars=12,
        setup_invalidation=4078.0,
        strictly_after_base=True,
        model_aware=True,
    )
    assert conf["status"] == "CONFIRMED"
    assert conf["wait_bars"] == 3
    assert conf["confirmed_checkpoint_index_1m"] == 43
    assert int(conf["new_decision_timestamp_ms"]) == base_ts + 3 * 60_000

    closed_m1 = bars_1m[: base_idx + 1]
    fwd_m1 = bars_1m[base_idx + 1 :]
    feat = {
        "htf_alignment_state": "ALIGNED",
        "sweep_confirmed": True,
        "sweep_reclaimed": True,
        "mss_confirmed": True,
        "displacement_valid": True,
    }

    # Old A1 stays SAME_CUTOFF_PRE_ENTRY_FILTER_ONLY (delayed_entry_executed == False)
    res_a1 = evaluate_r3_counterfactual_stop_and_target_v17931212(
        variant_id="A1_ENTRY_CONFIRMATION_RETEST_ONLY",
        opportunity_id="opp_synth_1",
        episode_key="ep_synth_1",
        model_id="SMC_M1_CONTINUATION",
        direction="LONG",
        decision_timestamp_ms=base_ts,
        baseline_entry=4085.2,
        baseline_sl=4080.0,
        baseline_tp=4122.0,
        closed_m1=closed_m1,
        closed_m5=ds["5m"],
        closed_m15=ds["15m"],
        closed_h1=ds["1H"],
        closed_h4=ds["4H"],
        forward_bars=fwd_m1,
        pre_entry_features=feat,
        fill_mode="NEXT_BAR_OPEN_BID_ASK",
    )
    assert res_a1["entry_timing_contract"] == "SAME_CUTOFF_PRE_ENTRY_FILTER_ONLY"
    assert res_a1["delayed_entry_executed"] is False

    # True delayed variants (A1B, D1..D4) execute genuine post-signal delayed entry with wait_bars >= 1
    for vid in TRUE_DELAYED_ENTRY_VARIANTS_V17931213:
        res_d = evaluate_r3_counterfactual_stop_and_target_v17931212(
            variant_id=vid,
            opportunity_id="opp_synth_1",
            episode_key="ep_synth_1",
            model_id="SMC_M1_CONTINUATION",
            direction="LONG",
            decision_timestamp_ms=base_ts,
            baseline_entry=4085.2,
            baseline_sl=4080.0,
            baseline_tp=4122.0,
            closed_m1=closed_m1,
            closed_m5=ds["5m"],
            closed_m15=ds["15m"],
            closed_h1=ds["1H"],
            closed_h4=ds["4H"],
            forward_bars=fwd_m1,
            pre_entry_features=feat,
            fill_mode="NEXT_BAR_OPEN_BID_ASK",
        )
        assert res_d["delayed_entry_executed"] is True
        assert 1 <= int(res_d["wait_bars"]) <= 12
        assert int(res_d["new_decision_timestamp_ms"]) > base_ts
        if res_d["filled"]:
            assert int(res_d["entry_fill_ts"]) >= int(res_d["new_decision_timestamp_ms"])
            assert float(res_d["planned_net_risk_usd"]) <= 2.500001


def test_p0_e1_shared_cross_adapter_parity_and_1pos_portfolio_lock() -> None:
    ds = _build_synthetic_bullish_mtf_dataset()
    bars_1m = ds["1m"]
    base_ts = int(bars_1m[40]["close_time"])
    common_kw = {
        "variant_id": "D4_CAUSAL_UNICORN_HTF_FULL_PIPELINE",
        "opportunity_id": "opp_ad_1",
        "episode_key": "ep_ad_1",
        "model_id": "SMC_M1_CONTINUATION",
        "direction": "LONG",
        "decision_timestamp_ms": base_ts,
        "baseline_entry": 4085.2,
        "baseline_sl": 4080.0,
        "baseline_tp": 4122.0,
        "closed_m1": bars_1m[:41],
        "closed_m5": ds["5m"],
        "closed_m15": ds["15m"],
        "closed_h1": ds["1H"],
        "closed_h4": ds["4H"],
        "forward_bars": bars_1m[41:65],
        "pre_entry_features": {
            "htf_alignment_state": "ALIGNED",
            "mss_confirmed": True,
            "displacement_valid": True,
        },
        "fill_mode": "NEXT_BAR_OPEN_BID_ASK",
    }
    outs = [
        evaluate_shared_delayed_entry_adapter_v17931213(
            execution_context_mode=ctx,
            activation_flags={"LIVE": False, "PAPER": False, "enabled_in_production": False},
            **common_kw,
        )
        for ctx in ("REPLAY_BACKTEST", "MARKET_SHADOW", "PRODUCTION_ORCHESTRATOR_DRY_RUN")
    ]
    c0, c1, c2 = ({k: v for k, v in o.items() if k != "adapter_execution_context_mode"} for o in outs)
    assert c0 == c1 == c2
    assert outs[2]["adapter_live_orders_emitted"] == 0
    assert outs[2]["adapter_default_activation_state"] == "OFF"
    with pytest.raises(PermissionError):
        evaluate_shared_delayed_entry_adapter_v17931213(
            execution_context_mode="PRODUCTION_ORCHESTRATOR_DRY_RUN",
            activation_flags={"enabled_in_production": True},
            **common_kw,
        )

    # Overlapping delayed episodes compete honestly in 1-pos portfolio
    rows = [
        {
            "opportunity_id": "opp_1",
            "status": "EXECUTED",
            "new_decision_timestamp_ms": 1_000_000,
            "filled": True,
            "entry_fill_ts": 1_060_000,
            "exit_fill_ts": 1_600_000,
            "is_closed": True,
            "outcome_label": "WIN",
            "realized_net_pnl_usd": 5.0,
            "realized_r_net": 2.0,
        },
        {
            "opportunity_id": "opp_2_blocked",
            "status": "EXECUTED",
            "new_decision_timestamp_ms": 1_300_000,
            "filled": True,
            "entry_fill_ts": 1_360_000,
            "exit_fill_ts": 1_800_000,
            "is_closed": True,
            "outcome_label": "LOSS",
            "realized_net_pnl_usd": -2.5,
            "realized_r_net": -1.0,
        },
    ]
    port = _replay_chronological_1pos_portfolio_from_matched_rows_v17931213(
        "D4_CAUSAL_UNICORN_HTF_FULL_PIPELINE", "NEXT_BAR_OPEN_BID_ASK", rows
    )
    assert port["scheduled_episodes_count"] == 1
    assert port["blocked_by_open_position_count"] == 1
    assert port["position_overlap_violations"] == 0


def test_p0_e2_e3_structural_htf_canonical_ids_and_dynamic_ledger_fail_closed() -> None:
    ds = _build_synthetic_bullish_mtf_dataset()
    close_ts = build_close_ts_index_v179312(ds)
    base_ts = int(ds["1m"][40]["close_time"])

    # Warmup fail-closed
    ok_empty, rsn_empty = _check_causal_htf_alignment_at_ts_v179312(
        {"1H": [], "4H": [], "15m": [], "5m": []},
        {"1H": [], "4H": [], "15m": [], "5m": []},
        base_ts,
        "LONG",
        use_structural_evaluator=True,
    )
    assert ok_empty is False
    assert any(
        tok in str(rsn_empty.get("rejection_reason") or "")
        for tok in ("MISSING", "UNDETERMINED", "CONFLICT")
    )

    # Bullish structural alignment passes LONG and rejects SHORT across ablation modes
    for ab_m in ("H4_ONLY", "H1_ONLY", "M15_ONLY", "M15_H1_ONLY", "FULL_MTF"):
        ok_long, _ = _check_causal_htf_alignment_at_ts_v179312(
            ds,
            close_ts,
            base_ts,
            "LONG",
            use_structural_evaluator=True,
            ablation_mode=ab_m,
        )
        ok_short, _ = _check_causal_htf_alignment_at_ts_v179312(
            ds,
            close_ts,
            base_ts,
            "SHORT",
            use_structural_evaluator=True,
            ablation_mode=ab_m,
        )
        assert ok_long is True
        assert ok_short is False

    # Detector-bound canonical swing/pool IDs verify cleanly under rebuild_materialized_intent_proof_v1793127
    dec = {
        "opportunity_id": "opp_canon_1",
        "model_id": "SMC_M1_CONTINUATION",
        "direction": "LONG",
        "checkpoint_index_1m": 40,
        "decision_timestamp_ms": base_ts,
        "entry_reference": 4085.2,
        "selected_sl": 4080.0,
        "take_profit": 4122.0,
        "structural_invalidation_price": 4078.0,
        "selected_break_id": f"bos_LONG_{ds['1m'][43]['open_time']}",
    }
    c_idx = build_full_intervention_candidate_index_v1793121([dec], ds, "pol_hash", close_ts_index=close_ts)
    pol = ExecutableResearchPolicyV179312(
        variant_id="SI8_TEST",
        base_cohort_variant="A1_V1792_SL_TP_BASELINE",
        intervention_type="ENTRY_SL_TP_COMBINED_EXECUTABLE",
        hypothesis_description="Test detector-bound canonical swing/pool IDs",
        cited_dev_loss_cause="MTF_UNICORN_TEST",
        require_causal_h1_h4_alignment=True,
        wait_for_confirmed_mss_and_displacement=True,
        max_wait_bars=12,
        rederive_outer_structural_stop=True,
        rederive_structural_liquidity_target=True,
    )
    mat = emit_causal_order_intents_v1793121(
        dec, pol, candidate_index=c_idx, datasets=ds, strict_v17931213_mode=True
    )
    assert mat["accepted"] is True
    assert str(mat["intent_id"]).startswith("intent_")
    _final_rec, _final_proof, final_rej = rebuild_materialized_intent_proof_v1793127(
        dec, mat, ds, dataset_sha256=CANONICAL_DATASET_SHA256_V17931213
    )
    assert final_rej is None

    # P0-E3: Missing/empty ledger fails closed with UNKNOWN_MISSING_INTENT_LEDGER
    blocked_diag = build_si8_si9_278_materialization_diagnosis_v17931210(
        {"decision_ledger": []}, {"decision_ledger": []}, {}
    )
    assert blocked_diag["status"] == "BLOCKED"
    assert blocked_diag["materialization_status"] == "UNKNOWN_MISSING_INTENT_LEDGER"
    assert blocked_diag["materialized_intents_count"] is None

    # Dynamic non-zero ledger counts flow end-to-end without hardcoded 0/278/6
    dyn_si8_led = [
        {
            "opportunity_id": "opp_1",
            "model_id": "UNICORN_MODEL",
            "direction": "LONG",
            "decision_timestamp_ms": base_ts,
            "accepted": True,
            "rejection_reason": None,
            "entered_materialization_stage": True,
            "intent_id": "intent_abc123",
            "new_decision_timestamp_ms": base_ts + 180_000,
        },
        {
            "opportunity_id": "opp_2",
            "model_id": "UNICORN_MODEL",
            "direction": "LONG",
            "decision_timestamp_ms": base_ts,
            "accepted": False,
            "rejection_reason": "SUPPRESSED_BY_ARBITRATION",
            "entered_materialization_stage": False,
        },
    ]
    dyn_diag = build_si8_si9_278_materialization_diagnosis_v17931210(
        {"decision_ledger": dyn_si8_led},
        {"decision_ledger": dyn_si8_led},
        {"opp_1": {"proof_hash": "hash_opp_1"}, "opp_2": {"proof_hash": "hash_opp_2"}},
    )
    assert dyn_diag["status"] == "PASS"
    assert dyn_diag["proof_eligible_candidates_count"] == 2
    assert dyn_diag["arbitration_suppressed_count"] == 1
    assert dyn_diag["post_arbitration_rejected_count"] == 0
    assert dyn_diag["materialized_intents_count"] == 1
    assert dyn_diag["si8_materialized_intent_ids"] == ["intent_abc123"]

    r2_ver = verify_r2_causal_mtf_unicorn_provenance_v17931212(
        {"status": "PASS"},
        {
            "canonical_universe_total": 63085,
            "causal_detector_funnel_counts": {
                "unicorn_confirmed_breaker_fvg_overlap": 100,
                "fvg_only_without_breaker_overlap": 60000,
                "unicorn_unknown_or_absent": 2985,
            },
        },
        {"si8_si9_dynamic_funnel_diagnosis": dyn_diag},
        raw_si8_decision_ledger=dyn_si8_led,
    )
    assert r2_ver["status"] == "PASS"


_CACHED_NEG66_RES: Dict[str, Any] = {}


def _get_neg66_res() -> Dict[str, Any]:
    global _CACHED_NEG66_RES
    if not _CACHED_NEG66_RES:
        _CACHED_NEG66_RES = run_negative_controls_v01_to_v66_v17931213(
            run_id="pytest_v13_neg66",
            tested_sha="b" * 40,
        )
    return _CACHED_NEG66_RES


def test_p0_e4_report_parser_callgraph_and_all_66_negative_controls_summary() -> None:
    cg = build_v17931213_callgraph_and_defect_matrix()
    assert cg["version"] == VERSION_V17931213
    assert cg["repaired_defects_count"] == 7
    assert cg["anchored_symbols_count"] >= 20

    neg_res = _get_neg66_res()
    assert neg_res["status"] == "PASS", f"Failed controls: {neg_res['violations']}"
    assert neg_res["controls_count"] == 66
    assert neg_res["rejected_count"] == 66
    assert neg_res["all_rejected"] is True
    assert neg_res["temp_dirs_cleaned"] is True


@pytest.mark.parametrize("ctrl_idx", list(range(66)))
def test_v01_to_v66_individual_fault_injected_negative_control(ctrl_idx: int) -> None:
    neg_res = _get_neg66_res()
    ctrls = neg_res["controls"]
    assert len(ctrls) == 66
    ctrl = ctrls[ctrl_idx]
    assert ctrl["rejected"] is True, f"Control {ctrl['control_id']} was not rejected: {ctrl}"
    assert ctrl["status"] == "PASS"
    assert ctrl["original_artifact_sha256"] != ctrl["mutated_artifact_sha256"]
    assert ctrl["temp_dir_cleaned"] is True
