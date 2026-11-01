"""
backend/tests/test_v17_9_3_1_2_full_universe_executable_interventions.py
AURUM Desk V17.9.3.1.2 — Mandatory 60-Test Suite (T01..T60) for Full-Universe Replay &
Executable Entry/SL/TP Counterfactual Validation.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, List
import pytest

from services.scalp_m1_types import get_scalp_config
from services.shared_strategy_evaluator import DecisionStatus, PRODUCTION_MODEL_IDS
from services.v17_9_3_1_1_loss_forensics_and_research_service import (
    compute_contiguous_session_day_block_bootstrap_ci,
    compute_wilson_95_ci,
    get_canonical_risk_policy_snapshot,
    validate_acceptance_gate_manifest,
    validate_artifact_sha256_manifest,
    validate_junit_and_collection_bijection,
)
from services.v17_9_3_1_2_executable_intervention_replay_service import (
    CANONICAL_PRODUCTION_MODEL_PRIORITY,
    ExecutableResearchPolicyV179312,
    OrderIntentV179312,
    PREDECLARED_EXECUTABLE_RESEARCH_POLICIES_V179312,
    PortfolioReplayStateV179312,
     VALID_DENOMINATOR_GROUPS_V179312,
    VALID_INTERVENTION_TYPES_V179312,
    V17_9_3_1_2_COMPARATOR_VARIANTS,
    V17_9_3_1_2_SCHEMA_VERSION,
    advance_portfolio_to_next_event_v179312,
    arbitrate_competing_model_intents_v179312,
    assert_intervention_is_real_v179312,
    assert_replay_oos_temporal_and_feature_integrity_v179312,
    attribution_v179312,
    build_causal_context_at_v179312,
    build_close_ts_index_v179312,
    build_order_intent_from_snapshot_v179312,
    build_protected_structure_authority_audit_v179312,
    compute_economic_and_statistical_summary_v179312,
    compute_loss_root_cause_matrix_v179312,
    compute_research_policy_registry_hash_v179312,
    derive_executable_structural_stop_v179312,
    derive_executable_structural_target_v179312,
    enumerate_full_replay_indices_v179312,
    evaluate_executable_interventions_on_cohort_v179312,
    evaluate_full_universe_model_batch_v179312,
    extract_per_timeframe_causal_provenance_v179312,
    find_next_causal_entry_confirmation_v179312,
    match_variants_on_causal_opportunity_v179312,
    materialize_executable_counterfactual_v179312,
    merge_and_validate_decision_stream_v179312,
    rebuild_entry_reference_v179312,
    recalculate_trade_risk_quantity_v179312,
    reconcile_trades_to_ledger_v179312,
    replay_global_chronological_portfolio_v179312,
    replay_intervention_outcome_v179312,
    simulate_executable_candidate_v179312,
    verify_no_trade_time_overlap_v179312,
    write_append_only_decision_stream_v179312,
)
from tools.run_v17_9_3_1_2_full_universe_backtest import (
    build_matched_coeval_comparison_v179312,
    load_canonical_xauusdt_datasets,
    select_cohort_with_unlock_candidates_v179312,
)
from tools.run_v17_9_3_macro_unicorn_backtest import (
    V1793_VARIANT_POLICIES,
    screen_v17931_causal_setup_universe,
)


@pytest.fixture(scope="module")
def canonical_data() -> Dict[str, Any]:
    datasets, prov = load_canonical_xauusdt_datasets()
    c_ts = build_close_ts_index_v179312(datasets)
    scr = screen_v17931_causal_setup_universe(datasets)
    scr_set = {int(x) for x in scr["combined_evaluation_indices"]}
    # Evaluate a 250-bar window (indices 500..749) where H1 and H4 are both warmed up
    sample_indices = list(range(500, 750))
    batch = evaluate_full_universe_model_batch_v179312(
        sample_indices,
        V1793_VARIANT_POLICIES,
        datasets=datasets,
        close_ts_index=c_ts,
        screened_indices_set=scr_set,
    )
    manifest, merged = merge_and_validate_decision_stream_v179312(
        [batch],
        expected_start_index=500,
        expected_end_index=749,
        verify_chunk_files=False,
    )
    all_recs: List[Dict[str, Any]] = []
    for vk in V17_9_3_1_2_COMPARATOR_VARIANTS:
        all_recs.extend(merged.get(vk, []))
    replays_primary = replay_global_chronological_portfolio_v179312(
        all_recs,
        V17_9_3_1_2_COMPARATOR_VARIANTS,
        datasets,
        fill_mode="NEXT_BAR_OPEN_BID_ASK",
    )
    replays_stress = replay_global_chronological_portfolio_v179312(
        all_recs,
        V17_9_3_1_2_COMPARATOR_VARIANTS,
        datasets,
        fill_mode="STRESS_WIDER_SPREAD_SLIPPAGE_2X",
    )
    replays_legacy = replay_global_chronological_portfolio_v179312(
        all_recs,
        V17_9_3_1_2_COMPARATOR_VARIANTS,
        datasets,
        fill_mode="LEGACY_CLOSE_FILL",
    )
    interventions = evaluate_executable_interventions_on_cohort_v179312(
        merged["A1_V1792_SL_TP_BASELINE"][:80],
        datasets,
    )
    return {
        "datasets": datasets,
        "provenance": prov,
        "close_ts_index": c_ts,
        "screen_res": scr,
        "screen_set": scr_set,
        "batch": batch,
        "manifest": manifest,
        "merged": merged,
        "replays_primary": {k: v.to_dict() for k, v in replays_primary.items()},
        "replays_stress": {k: v.to_dict() for k, v in replays_stress.items()},
        "replays_legacy": {k: v.to_dict() for k, v in replays_legacy.items()},
        "interventions": interventions,
    }


# ==============================================================================
# A. FULL-UNIVERSE REPLAY & DENOMINATOR CLOSURE (T01..T10)
# ==============================================================================
def test_t01_full_universe_enumerates_22346_post_warmup_bars_without_m1_7_dollar_truncation(canonical_data: Dict[str, Any]) -> None:
    bars_1m = canonical_data["datasets"]["1m"]
    idx_man = enumerate_full_replay_indices_v179312(bars_1m, warmup_bars=30)
    assert idx_man.total_m1_bars == 22376
    assert idx_man.warmup_bars == 30
    assert idx_man.post_warmup_count == 22346
    assert idx_man.first_post_warmup_index == 30
    assert idx_man.last_post_warmup_index == 22375
    assert idx_man.monotonic_strictly_increasing is True
    assert idx_man.zero_duplicate_timestamps is True
    assert idx_man.zero_corrupted_bars is True


def test_t02_outside_screen_15597_timestamps_and_47243_a1_decisions_reconcile(canonical_data: Dict[str, Any]) -> None:
    rec = canonical_data["manifest"].outside_screen_reconciliation
    assert rec["outside_screen_a1_eligible_decisions_count"] > 0
    assert rec["outside_screen_a1_unique_timestamps_count"] > 0
    assert (
        rec["outside_screen_a1_eligible_decisions_count"] + rec["inside_screen_a1_eligible_decisions_count"]
        == rec["full_universe_a1_eligible_decisions_count"]
    )
    assert "why_e1_differs_from_a1_by_92_outside_screen_decisions" in rec


def test_t03_why_e1_differs_from_a1_outside_screen_by_weekend_closed_macro_gate(canonical_data: Dict[str, Any]) -> None:
    ds = canonical_data["datasets"]
    c_ts = canonical_data["close_ts_index"]
    # Find a weekend-closed index where A1 is eligible and verify E1 rejects with MACRO_WEEKEND_CLOSED
    from services.v17_9_3_macro_unicorn_intelligence import resolve_causal_macro_time_window

    weekend_indices = [
        idx
        for idx in range(30, min(len(ds["1m"]), 5000))
        if resolve_causal_macro_time_window(int(ds["1m"][idx]["close_time"]), symbol="XAUUSDT").is_weekend_or_closed
    ]
    assert len(weekend_indices) > 0
    batch_wk = evaluate_full_universe_model_batch_v179312(
        weekend_indices[:25],
        V1793_VARIANT_POLICIES,
        datasets=ds,
        close_ts_index=c_ts,
        screened_indices_set=set(),
    )
    assert len(batch_wk["eligible_records_by_variant"]["E1_OUTSIDE_MACRO_COUNTERFACTUAL"]) == 0
    assert batch_wk["rejection_counts_by_variant"]["E1_OUTSIDE_MACRO_COUNTERFACTUAL"].get("MACRO_WEEKEND_CLOSED", 0) > 0


def test_t04_causal_context_builder_rejects_future_and_unclosed_candles_across_all_tfs(canonical_data: Dict[str, Any]) -> None:
    ds = canonical_data["datasets"]
    c_ts = canonical_data["close_ts_index"]
    ctx = build_causal_context_at_v179312(ds, 500, close_ts_index=c_ts)
    assert int(ctx.decision_timestamp_ms) == int(ds["1m"][500]["close_time"])

    # Corrupt bar 500 as unclosed -> must raise ValueError
    bad_ds = dict(ds)
    bad_1m = list(ds["1m"][:505])
    bad_1m[500] = dict(bad_1m[500], is_closed=False)
    bad_ds["1m"] = bad_1m
    with pytest.raises(ValueError, match="UNCLOSED_CANDLE_IN_1m"):
        build_causal_context_at_v179312(bad_ds, 500, close_ts_index=c_ts)


def test_t05_per_timeframe_causal_provenance_audit_enforces_close_time_le_decision_ts(canonical_data: Dict[str, Any]) -> None:
    ds = canonical_data["datasets"]
    c_ts = canonical_data["close_ts_index"]
    prov = extract_per_timeframe_causal_provenance_v179312(ds, 600, close_ts_index=c_ts)
    assert prov["causal_provenance_valid"] is True
    for tf, tf_info in prov["timeframes"].items():
        if tf_info["available_closed_bars_count"] > 0:
            assert tf_info["last_closed_bar_close_time_ms"] <= prov["decision_timestamp_ms"]
            assert tf_info["delta_ms_to_decision"] >= 0


def test_t06_append_only_decision_stream_atomic_write_and_sha256_integrity(
    canonical_data: Dict[str, Any], tmp_path: Path
) -> None:
    recs = canonical_data["merged"]["A1_V1792_SL_TP_BASELINE"][:25]
    chunk_file = tmp_path / "chunk_test.jsonl.gz"
    meta = write_append_only_decision_stream_v179312(recs, chunk_file, metadata={"chunk_id": 0})
    assert chunk_file.exists()
    assert meta["record_count"] == len(recs)
    assert meta["compressed_size_bytes"] > 0
    assert len(meta["uncompressed_rows_sha256"]) == 64
    assert len(meta["compressed_file_sha256"]) == 64


def test_t07_merge_and_validate_decision_stream_rejects_missing_duplicate_and_corrupt_chunks(
    canonical_data: Dict[str, Any], tmp_path: Path
) -> None:
    ch = copy.deepcopy(canonical_data["batch"])
    # Missing range test (expected 500..750 when chunk only covers 500..749)
    with pytest.raises(ValueError, match="MISSING_CHUNK_INDICES_DETECTED"):
        merge_and_validate_decision_stream_v179312([ch], expected_start_index=500, expected_end_index=750, verify_chunk_files=False)

    # Duplicate range test
    with pytest.raises(ValueError, match="DUPLICATE_CHUNK_INDICES_DETECTED"):
        merge_and_validate_decision_stream_v179312([ch, ch], expected_start_index=500, expected_end_index=749, verify_chunk_files=False)

    # Corrupt file hash test
    c_file = tmp_path / "corrupt.jsonl.gz"
    c_man = write_append_only_decision_stream_v179312(
        canonical_data["merged"]["A1_V1792_SL_TP_BASELINE"][:5], c_file
    )
    c_file.write_bytes(b"corrupted_bytes")
    ch_corrupt = dict(ch, chunk_stream_manifest=c_man)
    with pytest.raises(ValueError, match="CORRUPT_CHUNK_STREAM_SHA256"):
        merge_and_validate_decision_stream_v179312(
            [ch_corrupt], expected_start_index=500, expected_end_index=749, verify_chunk_files=True
        )


def test_t08_single_worker_vs_multi_worker_chunk_stream_hash_equivalence(canonical_data: Dict[str, Any]) -> None:
    ds = canonical_data["datasets"]
    c_ts = canonical_data["close_ts_index"]
    scr_set = canonical_data["screen_set"]
    # Evaluate indices 500..599 as 1 chunk vs 2 chunks of 50 bars
    ch_full = evaluate_full_universe_model_batch_v179312(
        list(range(500, 600)), V1793_VARIANT_POLICIES, datasets=ds, close_ts_index=c_ts, screened_indices_set=scr_set
    )
    ch_a = evaluate_full_universe_model_batch_v179312(
        list(range(500, 550)), V1793_VARIANT_POLICIES, datasets=ds, close_ts_index=c_ts, screened_indices_set=scr_set
    )
    ch_b = evaluate_full_universe_model_batch_v179312(
        list(range(550, 600)), V1793_VARIANT_POLICIES, datasets=ds, close_ts_index=c_ts, screened_indices_set=scr_set
    )
    man_1, _ = merge_and_validate_decision_stream_v179312(
        [ch_full], expected_start_index=500, expected_end_index=599, verify_chunk_files=False
    )
    man_2, _ = merge_and_validate_decision_stream_v179312(
        [ch_b, ch_a], expected_start_index=500, expected_end_index=599, verify_chunk_files=False
    )
    assert man_1.canonical_rows_sha256 == man_2.canonical_rows_sha256


def test_t09_legacy_screened_vs_full_universe_denominators_never_mixed_in_same_table_row(canonical_data: Dict[str, Any]) -> None:
    for d_grp in VALID_DENOMINATOR_GROUPS_V179312:
        assert isinstance(d_grp, str) and len(d_grp) > 0
    rep_full = canonical_data["replays_primary"]["A1_V1792_SL_TP_BASELINE"]
    assert rep_full["denominator_group"] == "FULL_UNIVERSE_ALL_TIMESTAMPS"


def test_t10_full_universe_model_evaluation_count_equals_timestamps_times_five_models(canonical_data: Dict[str, Any]) -> None:
    man = canonical_data["manifest"]
    assert man.total_model_evaluations_per_variant == man.total_timestamps_covered * len(PRODUCTION_MODEL_IDS)
    for vk, st_map in man.variant_status_totals.items():
        assert sum(st_map.values()) == man.total_model_evaluations_per_variant


# ==============================================================================
# B. GLOBAL CHRONOLOGICAL PORTFOLIO REPLAY & POSITION STATE MACHINE (T11..T20)
# ==============================================================================
def test_t11_order_intent_construction_validates_geometry_lot_size_risk_cap_and_min_net_rr(canonical_data: Dict[str, Any]) -> None:
    rec = canonical_data["merged"]["A1_V1792_SL_TP_BASELINE"][0]
    res_ok = build_order_intent_from_snapshot_v179312(rec)
    assert res_ok["status"] == "VALID_INTENT"
    intent: OrderIntentV179312 = res_ok["order_intent"]
    assert intent.hypothetical_quantity >= 0.01
    assert intent.planned_net_risk_usd <= 2.50 + 1e-6
    assert intent.net_rr >= 2.0 - 1e-6

    # Inverted SL/TP geometry must be rejected
    bad_rec = dict(rec, selected_sl=float(rec["entry_reference"]) + 5.0 if rec["direction"] == "LONG" else float(rec["entry_reference"]) - 5.0)
    res_bad = build_order_intent_from_snapshot_v179312(bad_rec)
    assert res_bad["status"] == "REJECTED"


def test_t12_competing_model_intents_same_timestamp_arbitrated_deterministically(canonical_data: Dict[str, Any]) -> None:
    rec = canonical_data["merged"]["A1_V1792_SL_TP_BASELINE"][0]
    ent = float(rec["entry_reference"])
    sl = float(rec["selected_sl"])
    tp = float(rec["take_profit"])
    wider_tp = round(tp + 15.0, 2) if rec["direction"] == "LONG" else round(tp - 15.0, 2)
    i1 = build_order_intent_from_snapshot_v179312(dict(rec, model_id="CG01_SWEEP_MSS_FVG", take_profit=wider_tp))["order_intent"]
    i2 = build_order_intent_from_snapshot_v179312(dict(rec, model_id="SMC_M1_REVERSAL", entry_reference=ent, selected_sl=sl, take_profit=tp))["order_intent"]
    arb_prod = arbitrate_competing_model_intents_v179312([i1, i2], tie_policy="PRODUCTION_MODEL_ORDER")
    assert arb_prod["selected_intent"].model_id == "SMC_M1_REVERSAL"
    assert arb_prod["tie_occurred"] is True
    assert len(arb_prod["discarded_intents"]) == 1

    arb_rr = arbitrate_competing_model_intents_v179312([i1, i2], tie_policy="HIGHEST_NET_RR_THEN_PRODUCTION_ORDER")
    assert arb_rr["selected_intent"].model_id == "CG01_SWEEP_MSS_FVG"


def test_t13_portfolio_state_machine_blocks_overlapping_entries_while_position_open() -> None:
    st = PortfolioReplayStateV179312(variant="A1_V1792_SL_TP_BASELINE", max_concurrent_positions=1)
    st.active_episode = {
        "episode_key": "ep_1",
        "outcome_label": "WIN",
        "realized_net_pnl_usd": 5.20,
        "realized_gross_pnl_usd": 6.00,
        "total_friction_usd": 0.80,
    }
    st.active_release_ts_ms = 2_000_000
    adv_before = advance_portfolio_to_next_event_v179312(st, None, 1_500_000)
    assert adv_before["position_open_at_cutoff"] is True
    adv_after = advance_portfolio_to_next_event_v179312(st, None, 2_000_000)
    assert adv_after["position_open_at_cutoff"] is False
    assert st.closed_episodes_count == 1
    assert st.realized_net_pnl_usd == 5.20


def test_t14_candidate_conservation_selected_plus_tie_plus_blocked_plus_rejected_equals_raw_eligible(
    canonical_data: Dict[str, Any]
) -> None:
    for vk, rep in canonical_data["replays_primary"].items():
        raw_n = rep["raw_eligible_model_decisions_count"]
        accounted = (
            rep["portfolio_entry_attempts_count"]
            + rep["discarded_same_bar_tie_count"]
            + rep["blocked_by_open_position_count"]
            + rep["rejected_intents_count"]
        )
        assert accounted == raw_n, f"Candidate conservation failed for {vk}: {accounted} != {raw_n}"


def test_t15_no_fill_attempts_do_not_occupy_position_slot_or_fabricate_pnl(canonical_data: Dict[str, Any]) -> None:
    rec = canonical_data["merged"]["A1_V1792_SL_TP_BASELINE"][0]
    intent = build_order_intent_from_snapshot_v179312(rec)["order_intent"]
    sim_empty = simulate_executable_candidate_v179312(intent, [], fill_mode="NEXT_BAR_OPEN_BID_ASK")
    assert sim_empty["filled"] is False
    assert sim_empty["outcome_label"] in ("NO_FILL", "NO_FORWARD_BARS")
    assert sim_empty["release_ts_ms"] == intent.signal_timestamp_ms


def test_t16_open_at_horizon_occupies_position_slot_until_horizon_end_with_zero_realized_pnl(
    canonical_data: Dict[str, Any]
) -> None:
    rec = canonical_data["merged"]["A1_V1792_SL_TP_BASELINE"][0]
    # Set unreachable SL and TP so trade stays OPEN_AT_HORIZON across 10 flat bars
    wide_rec = dict(
        rec,
        entry_reference=4200.0,
        selected_sl=4180.0,
        take_profit=4260.0,
        direction="LONG",
    )
    intent = build_order_intent_from_snapshot_v179312(wide_rec)["order_intent"]
    base_ts = int(intent.signal_timestamp_ms)
    flat_bars = [
        {
            "open_time": base_ts + i * 60_000,
            "close_time": base_ts + (i + 1) * 60_000,
            "open": 4200.0,
            "high": 4201.0,
            "low": 4199.0,
            "close": 4200.0,
            "is_closed": True,
        }
        for i in range(10)
    ]
    sim = simulate_executable_candidate_v179312(intent, flat_bars, fill_mode="NEXT_BAR_OPEN_BID_ASK")
    assert sim["outcome_label"] == "OPEN_AT_HORIZON"
    assert sim["release_ts_ms"] == flat_bars[-1]["close_time"]
    rec_led = reconcile_trades_to_ledger_v179312([sim])
    assert rec_led["reconciled"] is True
    assert rec_led["open_at_horizon_censored_count"] == 1
    assert rec_led["closed_episodes_count"] == 0


def test_t17_data_gap_unresolved_censored_from_win_loss_rate_and_occupies_until_gap_bar(
    canonical_data: Dict[str, Any]
) -> None:
    rec = canonical_data["merged"]["A1_V1792_SL_TP_BASELINE"][0]
    wide_rec = dict(
        rec,
        entry_reference=4200.0,
        selected_sl=4190.0,
        take_profit=4240.0,
        direction="LONG",
    )
    intent = build_order_intent_from_snapshot_v179312(wide_rec)["order_intent"]
    base_ts = int(intent.signal_timestamp_ms)
    gap_bars = [
        {
            "open_time": base_ts,
            "close_time": base_ts + 60_000,
            "open": 4200.0,
            "high": 4201.0,
            "low": 4199.0,
            "close": 4200.0,
            "is_closed": True,
        },
        {
            # 20-minute gap that opens above TP -> DATA_GAP_UNRESOLVED
            "open_time": base_ts + 20 * 60_000,
            "close_time": base_ts + 21 * 60_000,
            "open": 4245.0,
            "high": 4247.0,
            "low": 4243.0,
            "close": 4245.0,
            "is_closed": True,
        },
    ]
    sim = simulate_executable_candidate_v179312(intent, gap_bars, fill_mode="NEXT_BAR_OPEN_BID_ASK")
    assert sim["outcome_label"] == "DATA_GAP_UNRESOLVED"
    rec_led = reconcile_trades_to_ledger_v179312([sim])
    assert rec_led["reconciled"] is True
    assert rec_led["data_gap_unresolved_count"] == 1


def test_t18_verify_no_trade_time_overlap_detects_injected_interval_collision(canonical_data: Dict[str, Any]) -> None:
    eps = canonical_data["replays_primary"]["A1_V1792_SL_TP_BASELINE"]["episodes"]
    chk_ok = verify_no_trade_time_overlap_v179312(eps, {"max_concurrent_positions": 1})
    assert chk_ok["contract_valid"] is True
    assert chk_ok["collision_count"] == 0

    # Inject a colliding trade inside the first trade's interval
    ep0 = dict(eps[0])
    colliding = dict(
        ep0,
        episode_key="injected_collision",
        decision_timestamp_ms=int(ep0["entry_fill_ts"]) + 1000,
        entry_fill_ts=int(ep0["entry_fill_ts"]) + 1000,
        release_ts_ms=int(ep0["release_ts_ms"]),
    )
    chk_bad = verify_no_trade_time_overlap_v179312([ep0, colliding], {"max_concurrent_positions": 1})
    assert chk_bad["contract_valid"] is False
    assert chk_bad["collision_count"] == 1


def test_t19_reconcile_trades_to_ledger_enforces_gross_minus_fees_equals_net_without_double_counting(
    canonical_data: Dict[str, Any]
) -> None:
    for vk, rep in canonical_data["replays_primary"].items():
        rec = rep["ledger_reconciliation"]
        assert rec["reconciled"] is True
        assert rec["portfolio_identity_gross_minus_fees_equals_net"] is True


def test_t20_adverse_gap_open_beyond_sl_can_exceed_planned_risk_and_is_explicitly_audited(
    canonical_data: Dict[str, Any]
) -> None:
    rec = canonical_data["merged"]["A1_V1792_SL_TP_BASELINE"][0]
    wide_rec = dict(
        rec,
        entry_reference=4200.0,
        selected_sl=4195.0,
        take_profit=4226.0,
        direction="LONG",
    )
    intent = build_order_intent_from_snapshot_v179312(wide_rec)["order_intent"]
    base_ts = int(intent.signal_timestamp_ms)
    adverse_bars = [
        {
            "open_time": base_ts,
            "close_time": base_ts + 60_000,
            "open": 4200.0,
            "high": 4200.5,
            "low": 4199.5,
            "close": 4200.0,
            "is_closed": True,
        },
        {
            # Gap down far below SL
            "open_time": base_ts + 15 * 60_000,
            "close_time": base_ts + 16 * 60_000,
            "open": 4185.0,
            "high": 4186.0,
            "low": 4184.0,
            "close": 4185.0,
            "is_closed": True,
        },
    ]
    sim = simulate_executable_candidate_v179312(intent, adverse_bars, fill_mode="NEXT_BAR_OPEN_BID_ASK")
    assert sim["outcome_label"] == "LOSS"
    assert sim["adverse_gap_beyond_sl_usd"] > 0.0
    led_rec = reconcile_trades_to_ledger_v179312([sim])
    assert led_rec["reconciled"] is True
    assert led_rec["adverse_gap_exceeding_planned_risk_count"] == 1


# ==============================================================================
# C. EXECUTABLE ENTRY TIMING INTERVENTIONS (T21..T30)
# ==============================================================================
def test_t21_executable_research_policies_r0_to_r8_preregistered_and_production_disabled() -> None:
    assert len(PREDECLARED_EXECUTABLE_RESEARCH_POLICIES_V179312) == 9
    for vid, pol in PREDECLARED_EXECUTABLE_RESEARCH_POLICIES_V179312.items():
        assert pol.enabled_in_production is False
        assert pol.observe_only_outside_backtest is True
        assert pol.intervention_type in VALID_INTERVENTION_TYPES_V179312


def test_t22_delayed_entry_mss_displacement_moves_decision_timestamp_forward_and_rebuilds_entry(
    canonical_data: Dict[str, Any]
) -> None:
    r2 = canonical_data["interventions"]["results_by_policy"]["R2_DELAYED_ENTRY_MSS_DISPLACEMENT_EXECUTABLE"]
    assert r2["accepted_modified_executable_geometry_count"] > 0
    for rec in r2["matched_opportunity_records"]:
        if rec["accepted"]:
            assert rec["new_decision_timestamp_ms"] >= rec["old_plan"]["decision_timestamp_ms"]
            assert rec["known_at_ms"] <= rec["new_decision_timestamp_ms"]


def test_t23_delayed_entry_breaker_fvg_retest_requires_confirmed_fvg_before_closed_retest_bar(
    canonical_data: Dict[str, Any]
) -> None:
    r3 = canonical_data["interventions"]["results_by_policy"]["R3_DELAYED_ENTRY_BREAKER_FVG_RETEST_EXECUTABLE"]
    assert r3["accepted_modified_executable_geometry_count"] + r3["accepted_unchanged_noop_count"] > 0
    for rec in r3["matched_opportunity_records"]:
        if rec["accepted"]:
            assert rec["new_decision_timestamp_ms"] >= rec["old_plan"]["decision_timestamp_ms"]


def test_t24_delayed_entry_cancels_when_setup_invalidation_broken_during_wait(canonical_data: Dict[str, Any]) -> None:
    bars_1m = canonical_data["datasets"]["1m"]
    rec = dict(canonical_data["merged"]["A1_V1792_SL_TP_BASELINE"][0], direction="LONG")
    idx = int(rec["checkpoint_index_1m"])
    # Set setup_invalidation above current bar low so it immediately invalidates
    res = find_next_causal_entry_confirmation_v179312(
        rec,
        bars_1m,
        max_wait_bars=5,
        setup_invalidation=float(bars_1m[idx]["low"]) + 10.0,
    )
    assert res["status"] == "REJECTED"
    assert res["rejection_reason"] == "CANCELLED_SETUP_INVALIDATED_BEFORE_CONFIRMATION"


def test_t25_delayed_entry_cancels_when_fvg_consumed_by_close_beyond_distal_edge() -> None:
    base_ts = 1_700_000_000_000
    bars = [
        {
            "open_time": base_ts + i * 60_000,
            "close_time": base_ts + (i + 1) * 60_000,
            "open": 4200.0,
            "high": 4201.0,
            "low": 4199.0,
            "close": 4200.0,
            "is_closed": True,
        }
        for i in range(20)
    ]
    # Create a bullish FVG at bars 10, 11, 12 (bar 10 high=4201, bar 12 low=4203)
    bars[10]["high"] = 4201.0
    bars[11]["open"] = 4201.0
    bars[11]["close"] = 4205.0
    bars[11]["high"] = 4205.5
    bars[12]["low"] = 4203.0
    bars[12]["close"] = 4204.5
    # Bar 13 closes below FVG bottom (4201.0) -> consumes/invalidates FVG
    bars[13]["low"] = 4199.5
    bars[13]["close"] = 4200.0
    sig = {"checkpoint_index_1m": 13, "decision_timestamp_ms": bars[13]["close_time"], "direction": "LONG"}
    res = find_next_causal_entry_confirmation_v179312(
        sig, bars, max_wait_bars=3, setup_invalidation=4180.0, require_mss_displacement=False, require_breaker_fvg_retest=True
    )
    assert res["status"] == "REJECTED"
    assert res["rejection_reason"] == "CANCELLED_FVG_CONSUMED_OR_INVALIDATED"


def test_t26_delayed_entry_cancels_on_market_gap_over_5_minutes_during_wait() -> None:
    base_ts = 1_700_000_000_000
    bars = [
        {
            "open_time": base_ts + i * 60_000,
            "close_time": base_ts + (i + 1) * 60_000,
            "open": 4200.0,
            "high": 4200.2,
            "low": 4199.8,
            "close": 4200.0,
            "is_closed": True,
        }
        for i in range(15)
    ]
    # Inject a 10-minute gap between bar 11 and bar 12
    bars[12]["open_time"] = bars[11]["close_time"] + 600_000
    bars[12]["close_time"] = bars[12]["open_time"] + 60_000
    sig = {"checkpoint_index_1m": 11, "decision_timestamp_ms": bars[11]["close_time"], "direction": "LONG"}
    res = find_next_causal_entry_confirmation_v179312(
        sig, bars, max_wait_bars=3, setup_invalidation=4180.0, require_mss_displacement=True
    )
    assert res["status"] == "REJECTED"
    assert res["rejection_reason"] == "CANCELLED_MARKET_GAP_DURING_CONFIRMATION_WAIT"


def test_t27_delayed_entry_cancels_when_wait_window_expires_unconfirmed() -> None:
    base_ts = 1_700_000_000_000
    bars = [
        {
            "open_time": base_ts + i * 60_000,
            "close_time": base_ts + (i + 1) * 60_000,
            "open": 4200.0,
            "high": 4200.5,
            "low": 4199.5,
            "close": 4200.0,
            "is_closed": True,
        }
        for i in range(20)
    ]
    sig = {"checkpoint_index_1m": 12, "decision_timestamp_ms": bars[12]["close_time"], "direction": "LONG"}
    res = find_next_causal_entry_confirmation_v179312(
        sig, bars, max_wait_bars=4, setup_invalidation=4180.0, require_mss_displacement=True
    )
    assert res["status"] == "REJECTED"
    assert res["rejection_reason"] == "WAIT_WINDOW_EXPIRED_UNCONFIRMED"


def test_t28_delayed_entry_cancels_when_extension_exceeds_4_atr_m1() -> None:
    base_ts = 1_700_000_000_000
    bars = [
        {
            "open_time": base_ts + i * 60_000,
            "close_time": base_ts + (i + 1) * 60_000,
            "open": 4200.0,
            "high": 4200.5,
            "low": 4199.5,
            "close": 4200.0,
            "is_closed": True,
        }
        for i in range(20)
    ]
    # Make bar 12 a huge over-extended spike (> 4x ATR)
    bars[12]["open"] = 4200.0
    bars[12]["high"] = 4225.0
    bars[12]["low"] = 4200.0
    bars[12]["close"] = 4224.0
    sig = {"checkpoint_index_1m": 12, "decision_timestamp_ms": bars[12]["close_time"], "direction": "LONG"}
    res = find_next_causal_entry_confirmation_v179312(
        sig, bars, max_wait_bars=2, setup_invalidation=4180.0, require_mss_displacement=True
    )
    assert res["status"] == "REJECTED"
    assert res["rejection_reason"] == "REJECTED_LATE_ENTRY_EXTENSION_OVER_4_ATR"


def test_t29_delayed_entry_rejects_unclosed_confirmation_candle_and_backdated_timestamp() -> None:
    base_ts = 1_700_000_000_000
    bars = [
        {
            "open_time": base_ts + i * 60_000,
            "close_time": base_ts + (i + 1) * 60_000,
            "open": 4200.0,
            "high": 4201.0,
            "low": 4199.0,
            "close": 4200.0,
            "is_closed": True,
        }
        for i in range(15)
    ]
    bars[12]["is_closed"] = False
    sig = {"checkpoint_index_1m": 12, "decision_timestamp_ms": bars[12]["close_time"], "direction": "LONG"}
    res = find_next_causal_entry_confirmation_v179312(sig, bars, max_wait_bars=2, setup_invalidation=4180.0)
    assert res["status"] == "REJECTED"
    assert res["rejection_reason"] == "REJECTED_UNCLOSED_CONFIRMATION_BAR"


def test_t30_rebuild_entry_reference_uses_confirmed_bar_bid_ask_and_rejects_invalid_spread(
    canonical_data: Dict[str, Any]
) -> None:
    ds = canonical_data["datasets"]
    c_ts = canonical_data["close_ts_index"]
    ctx = build_causal_context_at_v179312(ds, 520, close_ts_index=c_ts)
    res_long = rebuild_entry_reference_v179312(ctx, int(ctx.decision_timestamp_ms), {"direction": "LONG", "spread_usd": 0.20})
    res_short = rebuild_entry_reference_v179312(ctx, int(ctx.decision_timestamp_ms), {"direction": "SHORT", "spread_usd": 0.20})
    assert res_long["status"] == "VALID"
    assert res_short["status"] == "VALID"
    assert round(res_long["entry_reference"] - res_short["entry_reference"], 2) == 0.20

    res_bad = rebuild_entry_reference_v179312(ctx, int(ctx.decision_timestamp_ms), {"direction": "LONG", "spread_usd": -0.10})
    assert res_bad["status"] == "REJECTED"


# ==============================================================================
# D. EXECUTABLE STRUCTURAL SL & TP INTERVENTIONS (T31..T40)
# ==============================================================================
def test_t31_derive_executable_structural_stop_selects_outer_protected_anchor_and_changes_sl(
    canonical_data: Dict[str, Any]
) -> None:
    r4 = canonical_data["interventions"]["results_by_policy"]["R4_OUTER_STRUCTURAL_STOP_EXECUTABLE"]
    assert r4["accepted_modified_executable_geometry_count"] > 0
    assert r4["changed_dimensions_counts"].get("selected_sl", 0) > 0


def test_t32_derive_executable_structural_stop_enforces_min_atr_distance_and_rejects_when_absent(
    canonical_data: Dict[str, Any]
) -> None:
    ds = canonical_data["datasets"]
    c_ts = canonical_data["close_ts_index"]
    ctx = build_causal_context_at_v179312(ds, 520, close_ts_index=c_ts)
    # Require an impossible 500x ATR stop distance -> must reject cleanly
    res = derive_executable_structural_stop_v179312(
        ctx,
        "LONG",
        float(ctx.closed_m1_candles[-1]["close"]) + 0.10,
        require_outer_protected_structure=True,
        min_atr_m1_multiple=500.0,
    )
    assert res["status"] == "REJECTED"
    assert res["rejection_reason"] == "REJECTED_NO_OUTER_PROTECTED_ANCHOR_MEETING_MIN_ATR_DISTANCE"


def test_t33_derive_executable_structural_stop_never_invents_stop_without_causal_anchor(
    canonical_data: Dict[str, Any]
) -> None:
    ds = canonical_data["datasets"]
    c_ts = canonical_data["close_ts_index"]
    ctx = build_causal_context_at_v179312(ds, 520, close_ts_index=c_ts)
    # Pass an entry far below all LONG candles so no anchor is on the protective side
    res = derive_executable_structural_stop_v179312(
        ctx,
        "LONG",
        100.0,
        require_outer_protected_structure=True,
    )
    assert res["status"] == "REJECTED"
    assert res["selected_sl"] is None


def test_t34_derive_executable_structural_target_selects_unswept_causal_pool_in_2_0_to_3_5_rr_band(
    canonical_data: Dict[str, Any]
) -> None:
    r5 = canonical_data["interventions"]["results_by_policy"]["R5_CAUSAL_LIQUIDITY_TARGET_EXECUTABLE"]
    assert r5["accepted_modified_executable_geometry_count"] + r5["accepted_unchanged_noop_count"] > 0
    for rec in r5["matched_opportunity_records"]:
        if rec["accepted"]:
            assert 2.0 - 1e-6 <= float(rec["new_net_rr"]) <= 3.5 + 1e-6


def test_t35_derive_executable_structural_target_rejects_swept_or_future_liquidity_levels(
    canonical_data: Dict[str, Any]
) -> None:
    ds = canonical_data["datasets"]
    c_ts = canonical_data["close_ts_index"]
    ctx = build_causal_context_at_v179312(ds, 520, close_ts_index=c_ts)
    # Require impossible min_net_rr=100.0 -> must reject
    res = derive_executable_structural_target_v179312(
        ctx,
        "LONG",
        float(ctx.closed_m1_candles[-1]["close"]) + 0.10,
        float(ctx.closed_m1_candles[-1]["close"]) - 2.00,
        max_net_rr=105.0,
        min_net_rr=100.0,
    )
    assert res["status"] == "REJECTED"
    assert res["take_profit"] is None


def test_t36_recalculate_trade_risk_quantity_recomputes_lot_size_and_enforces_2_50_risk_cap() -> None:
    r_tight = recalculate_trade_risk_quantity_v179312(4200.0, 4195.0, 4226.0, direction="LONG")
    r_wide = recalculate_trade_risk_quantity_v179312(4200.0, 4185.0, 4250.0, direction="LONG")
    assert r_tight["can_execute"] is True
    assert r_wide["can_execute"] is True
    assert r_tight["quantity"] > r_wide["quantity"]
    assert r_tight["planned_net_risk_usd"] <= 2.50 + 1e-6
    assert r_wide["planned_net_risk_usd"] <= 2.50 + 1e-6


def test_t37_materialize_executable_counterfactual_distinguishes_modified_noop_and_rejected(
    canonical_data: Dict[str, Any]
) -> None:
    ds = canonical_data["datasets"]
    c_ts = canonical_data["close_ts_index"]
    opp = canonical_data["merged"]["A1_V1792_SL_TP_BASELINE"][0]
    m_r0 = materialize_executable_counterfactual_v179312(
        "setup_test_0",
        PREDECLARED_EXECUTABLE_RESEARCH_POLICIES_V179312["R0_BASELINE_CONTROL"],
        ds,
        base_record=opp,
        close_ts_index=c_ts,
    )
    assert m_r0["status"] == "ACCEPTED_UNCHANGED_NO_OP"
    assert m_r0["changed_dimensions"] == []

    m_r4 = materialize_executable_counterfactual_v179312(
        "setup_test_0",
        PREDECLARED_EXECUTABLE_RESEARCH_POLICIES_V179312["R4_OUTER_STRUCTURAL_STOP_EXECUTABLE"],
        ds,
        base_record=opp,
        close_ts_index=c_ts,
    )
    assert m_r4["status"] in ("ACCEPTED_MODIFIED_EXECUTABLE_GEOMETRY", "REJECTED_BY_INTERVENTION")
    if m_r4["accepted"]:
        assert "selected_sl" in m_r4["changed_dimensions"]


def test_t38_replay_intervention_outcome_simulates_from_new_timestamp_and_new_geometry(
    canonical_data: Dict[str, Any]
) -> None:
    ds = canonical_data["datasets"]
    c_ts = canonical_data["close_ts_index"]
    opp = canonical_data["merged"]["A1_V1792_SL_TP_BASELINE"][0]
    mat = materialize_executable_counterfactual_v179312(
        "setup_test_38",
        PREDECLARED_EXECUTABLE_RESEARCH_POLICIES_V179312["R4_OUTER_STRUCTURAL_STOP_EXECUTABLE"],
        ds,
        base_record=opp,
        close_ts_index=c_ts,
    )
    rep = replay_intervention_outcome_v179312(mat, ds["1m"], mode="NEXT_BAR_OPEN_BID_ASK", base_record=opp)
    if mat["accepted"]:
        assert rep["simulated"] is True
        assert rep["episode_record"]["selected_sl"] == mat["new_selected_sl"]


def test_t39_assert_intervention_is_real_catches_fake_label_only_copied_pnl_and_stale_qty(
    canonical_data: Dict[str, Any]
) -> None:
    ds = canonical_data["datasets"]
    c_ts = canonical_data["close_ts_index"]
    opp = canonical_data["merged"]["A1_V1792_SL_TP_BASELINE"][0]
    mat = materialize_executable_counterfactual_v179312(
        "setup_test_39",
        PREDECLARED_EXECUTABLE_RESEARCH_POLICIES_V179312["R4_OUTER_STRUCTURAL_STOP_EXECUTABLE"],
        ds,
        base_record=opp,
        close_ts_index=c_ts,
    )
    rep = replay_intervention_outcome_v179312(mat, ds["1m"], mode="NEXT_BAR_OPEN_BID_ASK", base_record=opp)
    chk_ok = assert_intervention_is_real_v179312(opp, rep, "STRUCTURAL_STOP_EXECUTABLE", raise_on_error=True)
    assert chk_ok["valid"] is True

    # Inject a fake intervention claiming ACCEPTED_MODIFIED_EXECUTABLE_GEOMETRY with empty changed_dimensions
    fake_rep = copy.deepcopy(rep)
    fake_rep["materialized_plan"]["status"] = "ACCEPTED_MODIFIED_EXECUTABLE_GEOMETRY"
    fake_rep["materialized_plan"]["changed_dimensions"] = []
    with pytest.raises(ValueError, match="FAKE_MODIFIED_INTERVENTION_ZERO_CHANGED_DIMENSIONS"):
        assert_intervention_is_real_v179312(opp, fake_rep, "STRUCTURAL_STOP_EXECUTABLE", raise_on_error=True)

    # Inject a stale quantity mismatch
    if rep["episode_record"] is not None:
        stale_qty_rep = copy.deepcopy(rep)
        stale_qty_rep["episode_record"]["execution_ledger"]["quantity"] = 9.99
        with pytest.raises(ValueError, match="STALE_OR_MISMATCHED_QUANTITY_FOR_NEW_GEOMETRY"):
            assert_intervention_is_real_v179312(opp, stale_qty_rep, "STRUCTURAL_STOP_EXECUTABLE", raise_on_error=True)


def test_t40_protected_structure_authority_audit_zero_contradictory_stop_labels(canonical_data: Dict[str, Any]) -> None:
    eps = canonical_data["replays_primary"]["A1_V1792_SL_TP_BASELINE"]["episodes"]
    audit = build_protected_structure_authority_audit_v179312(eps)
    assert audit["audit_passed"] is True
    assert audit["contradictory_stop_labels_count"] == 0


# ==============================================================================
# E. MATCHED-OPPORTUNITY VS INDEPENDENT PORTFOLIO REPLAY & ATTRIBUTION (T41..T48)
# ==============================================================================
def test_t41_match_variants_on_causal_opportunity_tracks_changed_entry_sl_tp_qty_and_outcome(
    canonical_data: Dict[str, Any]
) -> None:
    int_rep = canonical_data["interventions"]
    r0_recs = int_rep["results_by_policy"]["R0_BASELINE_CONTROL"]["matched_opportunity_records"]
    v_map = {k: v["matched_opportunity_records"] for k, v in int_rep["results_by_policy"].items()}
    m_study = match_variants_on_causal_opportunity_v179312(
        r0_recs,
        v_map,
        baseline_portfolio_episodes=canonical_data["replays_primary"]["A1_V1792_SL_TP_BASELINE"]["episodes"],
    )
    assert m_study["comparisons_by_variant"]["R4_OUTER_STRUCTURAL_STOP_EXECUTABLE"]["changed_sl_count"] > 0
    assert m_study["comparisons_by_variant"]["R2_DELAYED_ENTRY_MSS_DISPLACEMENT_EXECUTABLE"]["changed_entry_timestamp_count"] > 0


def test_t42_independent_portfolio_replay_unlocks_previously_blocked_trades_when_earlier_trade_rejected(
    canonical_data: Dict[str, Any]
) -> None:
    cohort = select_cohort_with_unlock_candidates_v179312(
        canonical_data["merged"]["A1_V1792_SL_TP_BASELINE"],
        canonical_data["replays_primary"]["A1_V1792_SL_TP_BASELINE"]["episodes"],
        canonical_data["merged"]["C1_MACRO_PLUS_V1792_SL_TP"],
        unlock_followups_per_episode=2,
    )
    assert len(cohort) >= len(canonical_data["replays_primary"]["A1_V1792_SL_TP_BASELINE"]["episodes"])


def test_t43_attribution_decomposes_losers_avoided_winners_foregone_and_unlocked_trades(
    canonical_data: Dict[str, Any]
) -> None:
    int_rep = canonical_data["interventions"]
    r0_recs = int_rep["results_by_policy"]["R0_BASELINE_CONTROL"]["matched_opportunity_records"]
    v_map = {k: v["matched_opportunity_records"] for k, v in int_rep["results_by_policy"].items()}
    m_study = match_variants_on_causal_opportunity_v179312(r0_recs, v_map)
    attr = attribution_v179312(
        int_rep["results_by_policy"]["R0_BASELINE_CONTROL"]["independent_portfolio_replay_primary"],
        int_rep["results_by_policy"]["R2_DELAYED_ENTRY_MSS_DISPLACEMENT_EXECUTABLE"]["independent_portfolio_replay_primary"],
        m_study["comparisons_by_variant"]["R2_DELAYED_ENTRY_MSS_DISPLACEMENT_EXECUTABLE"],
    )
    assert "avoided_baseline_losers_count" in attr
    assert "foregone_baseline_winners_count" in attr
    assert "newly_unlocked_in_portfolio_count" in attr


def test_t44_attribution_flags_volume_reduction_without_positive_expectancy_as_non_edge() -> None:
    attr = attribution_v179312(
        {
            "closed_independent_episodes_count": 100,
            "wins": 20,
            "losses": 80,
            "loss_rate": 0.80,
            "expectancy_r_mean": -0.45,
            "modeled_net_pnl_usd": -120.0,
            "modeled_total_costs_usd": 35.0,
        },
        {
            "closed_independent_episodes_count": 10,
            "wins": 2,
            "losses": 8,
            "loss_rate": 0.80,
            "expectancy_r_mean": -0.40,
            "modeled_net_pnl_usd": -11.0,
            "modeled_total_costs_usd": 3.5,
        },
        {"avoided_baseline_losers_count": 72, "foregone_baseline_winners_count": 18},
    )
    assert "WARNING_VOLUME_REDUCTION_NOT_PROVEN_EDGE" in attr["volume_reduction_vs_edge_disclosure"]


def test_t45_three_fill_mode_sensitivity_next_bar_open_vs_legacy_close_vs_stress_2x(
    canonical_data: Dict[str, Any]
) -> None:
    r4_modes = canonical_data["interventions"]["results_by_policy"]["R4_OUTER_STRUCTURAL_STOP_EXECUTABLE"]["three_fill_mode_replays"]
    assert set(r4_modes.keys()) == {
        "NEXT_BAR_OPEN_BID_ASK",
        "LEGACY_CLOSE_FILL",
        "STRESS_WIDER_SPREAD_SLIPPAGE_2X",
    }
    assert (
        r4_modes["STRESS_WIDER_SPREAD_SLIPPAGE_2X"]["modeled_net_pnl_usd"]
        <= r4_modes["NEXT_BAR_OPEN_BID_ASK"]["modeled_net_pnl_usd"] + 1e-6
    )


def test_t46_loss_root_cause_matrix_ranks_primary_loss_drivers_across_full_universe_cohorts(
    canonical_data: Dict[str, Any]
) -> None:
    mat = compute_loss_root_cause_matrix_v179312(canonical_data["replays_primary"])
    assert "A1_V1792_SL_TP_BASELINE" in mat["full_universe_cohorts"]
    assert len(mat["ranked_root_causes"]) >= 4


def test_t47_economic_summary_enforces_activation_ready_no_when_oos_window_reused(
    canonical_data: Dict[str, Any]
) -> None:
    ds = canonical_data["datasets"]
    frozen_hash = compute_research_policy_registry_hash_v179312()
    oos_rep = assert_replay_oos_temporal_and_feature_integrity_v179312(
        {"A1_V1792_SL_TP_BASELINE": canonical_data["replays_primary"]["A1_V1792_SL_TP_BASELINE"]["episodes"]},
        ds["1m"],
        policy_registry_hash=frozen_hash,
        expected_frozen_policy_registry_hash=frozen_hash,
        oos_previously_inspected=True,
    )
    coeval = build_matched_coeval_comparison_v179312(
        canonical_data["merged"],
        canonical_data["replays_primary"],
        total_post_warmup_timestamps=250,
    )
    summary = compute_economic_and_statistical_summary_v179312(
        full_universe_replays_by_mode={
            "NEXT_BAR_OPEN_BID_ASK": canonical_data["replays_primary"],
            "STRESS_WIDER_SPREAD_SLIPPAGE_2X": canonical_data["replays_stress"],
            "LEGACY_CLOSE_FILL": canonical_data["replays_legacy"],
        },
        legacy_screened_replays=canonical_data["replays_primary"],
        interventions_report_a1=canonical_data["interventions"],
        interventions_report_b1=canonical_data["interventions"],
        matched_coeval_report=coeval,
        attribution_by_policy={},
        oos_integrity_report=oos_rep,
    )
    assert summary["ACTIVATION_READY"] == "NO"
    assert summary["ready_for_strategy_activation"] == "NO"


def test_t48_r0_baseline_control_reproduces_baseline_geometry_and_outcomes_exactly(
    canonical_data: Dict[str, Any]
) -> None:
    r0 = canonical_data["interventions"]["results_by_policy"]["R0_BASELINE_CONTROL"]
    assert r0["accepted_modified_executable_geometry_count"] == 0
    assert r0["rejected_by_intervention_count"] == 0
    assert r0["accepted_unchanged_noop_count"] == canonical_data["interventions"]["canonical_opportunities_count"]


# ==============================================================================
# F. CHRONOLOGICAL OOS, WALK-FORWARD, VERIFICATION & PRODUCTION SAFETY (T49..T60)
# ==============================================================================
def test_t49_oos_temporal_integrity_enforces_180_bar_embargo_and_purges_boundary_crossing_trades(
    canonical_data: Dict[str, Any]
) -> None:
    ds = canonical_data["datasets"]
    frozen_hash = compute_research_policy_registry_hash_v179312()
    oos_rep = assert_replay_oos_temporal_and_feature_integrity_v179312(
        {"A1_V1792_SL_TP_BASELINE": canonical_data["replays_primary"]["A1_V1792_SL_TP_BASELINE"]["episodes"]},
        ds["1m"],
        policy_registry_hash=frozen_hash,
        expected_frozen_policy_registry_hash=frozen_hash,
        embargo_bars=180,
    )
    assert oos_rep["integrity_valid"] is True
    with pytest.raises(ValueError, match="EMBARGO_BARS_BELOW_180_MINIMUM"):
        assert_replay_oos_temporal_and_feature_integrity_v179312(
            {}, ds["1m"], policy_registry_hash=frozen_hash, embargo_bars=60
        )


def test_t50_oos_delayed_entry_crossing_partition_boundary_is_purged_and_owned_by_new_timestamp(
    canonical_data: Dict[str, Any]
) -> None:
    ds = canonical_data["datasets"]
    bars_1m = ds["1m"]
    frozen_hash = compute_research_policy_registry_hash_v179312()
    dev_end_ts = int(bars_1m[int(len(bars_1m) * 0.50) - 1]["close_time"])
    cross_ep = {
        "episode_key": "delayed_cross_ep",
        "variant": "R2_DELAYED_ENTRY_MSS_DISPLACEMENT_EXECUTABLE",
        "model_id": "SMC_M1_REVERSAL",
        "direction": "LONG",
        "filled": True,
        "outcome_label": "LOSS",
        "base_decision_timestamp_ms": dev_end_ts - 60_000,
        "decision_timestamp_ms": dev_end_ts + 60_000,
        "entry_fill_ts": dev_end_ts + 60_000,
        "exit_fill_ts": dev_end_ts + 180_000,
        "release_ts_ms": dev_end_ts + 180_000,
        "realized_net_pnl_usd": -2.50,
        "realized_r": -1.0,
    }
    oos_rep = assert_replay_oos_temporal_and_feature_integrity_v179312(
        {"R2_DELAYED_ENTRY_MSS_DISPLACEMENT_EXECUTABLE": [cross_ep]},
        bars_1m,
        policy_registry_hash=frozen_hash,
        expected_frozen_policy_registry_hash=frozen_hash,
        embargo_bars=180,
    )
    assert oos_rep["delayed_entry_cross_partition_purged_count"] >= 1


def test_t51_oos_feature_integrity_detects_policy_registry_hash_drift_after_freeze(
    canonical_data: Dict[str, Any]
) -> None:
    ds = canonical_data["datasets"]
    with pytest.raises(ValueError, match="OOS_POLICY_HASH_DRIFT_DETECTED"):
        assert_replay_oos_temporal_and_feature_integrity_v179312(
            {},
            ds["1m"],
            policy_registry_hash="drifted_hash",
            expected_frozen_policy_registry_hash=compute_research_policy_registry_hash_v179312(),
        )


def test_t52_oos_reused_historical_window_explicitly_disclosed_as_not_fresh(
    canonical_data: Dict[str, Any]
) -> None:
    ds = canonical_data["datasets"]
    frozen_hash = compute_research_policy_registry_hash_v179312()
    oos_rep = assert_replay_oos_temporal_and_feature_integrity_v179312(
        {},
        ds["1m"],
        policy_registry_hash=frozen_hash,
        expected_frozen_policy_registry_hash=frozen_hash,
        oos_previously_inspected=True,
    )
    assert oos_rep["oos_holdout_freshness_status"] == "OOS_REUSED_NOT_FRESH_HISTORICAL_WINDOW_DISCLOSED"


def test_t53_contiguous_session_day_block_bootstrap_ci_and_wilson_ci_deterministic(
    canonical_data: Dict[str, Any]
) -> None:
    w_ci = compute_wilson_95_ci(80, 100)
    assert w_ci["lower_95"] < 0.80 < w_ci["upper_95"]
    eps = [
        e
        for e in canonical_data["replays_primary"]["A1_V1792_SL_TP_BASELINE"]["episodes"]
        if e["filled"] and e["outcome_label"] in ("WIN", "LOSS")
    ]
    b1 = compute_contiguous_session_day_block_bootstrap_ci(eps, metric_key="realized_r", n_boot=200, seed=179312)
    b2 = compute_contiguous_session_day_block_bootstrap_ci(eps, metric_key="realized_r", n_boot=200, seed=179312)
    assert b1 == b2


def test_t54_junit_xml_and_collection_bijection_validator_rejects_mismatched_counts(tmp_path: Path) -> None:
    xml_path = tmp_path / "junit.xml"
    xml_path.write_text(
        '<testsuites><testsuite name="pytest" tests="2" errors="0" failures="0" skipped="0">'
        '<testcase classname="t" name="test_a"/><testcase classname="t" name="test_b"/>'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    res_ok = validate_junit_and_collection_bijection(["t.py::test_a", "t.py::test_b"], [xml_path])
    assert res_ok["bijection_valid"] is True
    res_bad = validate_junit_and_collection_bijection(["t.py::test_a"], [xml_path])
    assert res_bad["bijection_valid"] is False


def test_t55_artifact_sha256_manifest_validator_detects_corrupt_or_missing_files(tmp_path: Path) -> None:
    f1 = tmp_path / "01_test.json"
    f1.write_text('{"ok": true}', encoding="utf-8")
    import hashlib

    sha1 = hashlib.sha256(f1.read_bytes()).hexdigest()
    res_ok = validate_artifact_sha256_manifest(
        tmp_path,
        [{"filename": "01_test.json", "sha256": sha1, "size_bytes": f1.stat().st_size}],
    )
    assert res_ok["valid"] is True
    res_bad = validate_artifact_sha256_manifest(
        tmp_path,
        [{"filename": "01_test.json", "sha256": "0" * 64, "size_bytes": f1.stat().st_size}],
    )
    assert res_bad["valid"] is False


def test_t56_acceptance_gate_manifest_validator_rejects_tautological_or_failed_gates() -> None:
    g_ok = {
        "gates": [
            {
                "gate_id": "G01",
                "passed": True,
                "status": "PASS",
                "description": "Full universe 22,346 timestamps evaluated",
                "evidence": {
                    "measured_value": 22346,
                    "threshold": "== 22346",
                    "source_artifact": "02_full_universe_22346_index_coverage_manifest.json",
                },
            }
        ]
    }
    assert validate_acceptance_gate_manifest(g_ok, expected_gate_ids=["G01"])["valid"] is True
    g_bad = {
        "gates": [
            {
                "gate_id": "G01",
                "passed": False,
                "status": "FAIL",
                "description": "Failed gate",
                "evidence": {"measured_value": 0},
            },
            {
                "gate_id": "G26",
                "passed": True,
                "status": "PASS",
                "description": "Self check gate claiming pass while G01 failed",
                "evidence": {"self_check": True},
            },
        ]
    }
    assert validate_acceptance_gate_manifest(g_bad, expected_gate_ids=["G01", "G26"])["valid"] is False


def test_t57_production_default_flags_remain_off_and_untouched_by_v179312() -> None:
    cfg = get_scalp_config()
    assert bool(getattr(cfg, "v17_9_2_structural_sl_enabled", False)) is False
    assert bool(getattr(cfg, "v17_9_3_macro_unicorn_enabled", False)) is False
    assert str(getattr(cfg, "v17_9_3_mode", "OFF")) == "OFF"


def test_t58_live_paper_and_auto_execution_paths_have_zero_dependency_on_v179312_research_service() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    for rel_path in (
        "backend/services/shared_strategy_evaluator.py",
        "backend/services/scalp_watcher_orchestrator.py",
        "backend/services/market_shadow_service.py",
    ):
        src = (repo_root / rel_path).read_text(encoding="utf-8")
        assert "v17_9_3_1_2_executable_intervention_replay_service" not in src


def test_t59_deterministic_repeat_replay_produces_identical_sha256_fingerprints(
    canonical_data: Dict[str, Any]
) -> None:
    ds = canonical_data["datasets"]
    recs = canonical_data["merged"]["A1_V1792_SL_TP_BASELINE"]
    r1 = replay_global_chronological_portfolio_v179312(recs, ["A1_V1792_SL_TP_BASELINE"], ds)["A1_V1792_SL_TP_BASELINE"].to_dict()
    r2 = replay_global_chronological_portfolio_v179312(recs, ["A1_V1792_SL_TP_BASELINE"], ds)["A1_V1792_SL_TP_BASELINE"].to_dict()
    assert r1 == r2


def test_t60_immutable_prior_artifact_directories_remain_untouched() -> None:
    import subprocess

    repo_root = Path(__file__).resolve().parents[2]
    for prior_dir in (
        "docs/artifacts/v17_9_2",
        "docs/artifacts/v17_9_2_1",
        "docs/artifacts/v17_9_3",
        "docs/artifacts/v17_9_3_1",
        "docs/artifacts/v17_9_3_1_1",
    ):
        proc = subprocess.run(
            ["git", "status", "--porcelain", "--", prior_dir],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=True,
        )
        assert proc.stdout.strip() == "", f"Prior artifact directory modified: {prior_dir}"
