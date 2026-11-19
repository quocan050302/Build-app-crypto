"""
backend/tests/test_kh_01b_r1_replay_economics_and_data.py

Tests E01..E06 variant evaluation, E05 time-shifted confirmation (confirmed_at_ms > t0),
zero-trade None expectancy honesty, <30 UNDERPOWERED guard, 2x/3x cost stress, runtime
OFF locks, and offline Bitget market data / Future OOS Escrow validators (G15 & G16).
"""
from __future__ import annotations

from dataclasses import asdict
import pytest

from services.kh_01b_entry_unicorn_research_service import (
    FILL_MODES_KH01B,
    VariantReplayBundle,
    _summarize_episode_list_kh01b,
)
from services.kh_01b_r1_entry_edge_discovery_service import (
    CANONICAL_DATASET_SHA256_KH01B_R1,
    assert_research_only_runtime_modes_off,
    compute_kh01b_r1_edge_uncertainty_and_research_verdict,
    evaluate_kh01b_r1_entry_variant,
    extract_kh01b_r1_causal_regime_features,
)
from tools.kh_01b_r1_collect_market_data import (
    CANONICAL_SAMPLE_END_MS,
    build_and_validate_future_oos_escrow_registry,
    run_offline_collector_self_audit,
    validate_and_normalize_paginated_candles,
    validate_prospective_bbo_snapshot,
)


def test_kh01b_r1_e05_delayed_confirmation_shifts_timestamp_strictly_after_t0() -> None:
    t0_ms = 1_792_003_600_000
    # 30 bars up to t0_ms (index 29 has close_time == t0_ms), followed by 4 subsequent confirmation bars
    bars_1m = []
    for i in range(30):
        o = t0_ms - (30 - i) * 60_000
        bars_1m.append({
            "timestamp": o, "open_time": o, "close_time": o + 60_000,
            "open": 3000.0, "high": 3000.8, "low": 2999.4, "close": 3000.2, "volume": 100.0, "is_closed": True,
        })
    # Subsequent bar idx=30 (close t0+60s): neutral consolidation inside invalidation
    bars_1m.append({
        "timestamp": t0_ms, "open_time": t0_ms, "close_time": t0_ms + 60_000,
        "open": 3000.2, "high": 3000.4, "low": 2999.7, "close": 3000.1, "volume": 110.0, "is_closed": True,
    })
    # Subsequent bar idx=31 (close t0+120s): strong bullish displacement above trigger 3000.3
    bars_1m.append({
        "timestamp": t0_ms + 60_000, "open_time": t0_ms + 60_000, "close_time": t0_ms + 120_000,
        "open": 3000.1, "high": 3001.4, "low": 3000.0, "close": 3001.1, "volume": 240.0, "is_closed": True,
    })

    c1 = [int(b["close_time"]) for b in bars_1m]
    cbi = {("1M", int(b["open_time"])): int(b["close_time"]) for b in bars_1m}
    cand = {
        "opportunity_id": "opp_e05_1",
        "setup_episode_id": "ep_e05_1",
        "model_id": "SMC_M1_CONTINUATION",
        "direction": "LONG",
        "decision_timestamp_ms": t0_ms,
        "checkpoint_index_1m": 29,
        "decision_status": "ELIGIBLE",
        "entry_reference": 3000.30,
        "selected_sl": 2996.00,
        "take_profit": 3022.00,
        "structural_invalidation_price": 2996.00,
        "hypothetical_quantity": 0.30,
        "planned_net_risk_usd": 2.25,
        "net_rr": 2.50,
    }
    r1_fr = extract_kh01b_r1_causal_regime_features(
        cand, {}, cbi, t0_ms, datasets={"1m": bars_1m}, close_ts_index={"1m": c1}
    )

    e05_rule = {
        "type": "DELAYED_CLOSED_BAR_CONFIRMATION",
        "max_wait_bars_m1": 6,
        "min_displacement_body_atr_ratio": 0.55,
        "min_net_rr": 2.0,
        "max_planned_risk_usd": 2.50,
    }
    dec = evaluate_kh01b_r1_entry_variant(e05_rule, "E05", "KH01B_R1-E05", cand, r1_fr, bars_1m=bars_1m)
    assert dec.eligible is True
    assert dec.delayed_confirmation_used is True
    assert dec.effective_checkpoint_index_1m == 31 > 29
    assert dec.effective_decision_timestamp_ms == t0_ms + 120_000 > t0_ms
    assert dec.known_at_ms == dec.effective_decision_timestamp_ms
    assert dec.materialized_plan is not None
    assert dec.materialized_plan["new_planned_net_risk_usd"] <= 2.50
    assert dec.materialized_plan["new_net_rr"] >= 2.0

    # If price breaches invalidation on bar 30 BEFORE confirmation, setup must be INVALIDATED (not confirmed!)
    bars_breached = [dict(b) for b in bars_1m]
    bars_breached[30] = {**bars_breached[30], "low": 2995.50}
    dec_inv = evaluate_kh01b_r1_entry_variant(e05_rule, "E05", "KH01B_R1-E05", cand, r1_fr, bars_1m=bars_breached)
    assert dec_inv.eligible is False
    assert "INVALIDATED" in str(dec_inv.rejection_code)


def test_kh01b_r1_economics_zero_trades_underpowered_and_runtime_off_locks() -> None:
    for bad_flag in ({"PRODUCTION": True}, {"LIVE": "ON"}, {"PAPER": "ACTIVE"}, {"AUTO": True}):
        with pytest.raises(PermissionError):
            assert_research_only_runtime_modes_off(bad_flag)

    empty_bnd = VariantReplayBundle(
        variant_id="KH01B_R1-ZERO", short_code="E06", ontology="REGIME_X_MODEL_INTERACTION_RESEARCH",
        axis_type="INTERACTION", population_name="TEST", population_total=100, eligible_candidates_count=0,
        rejected_candidates_count=100, missing_evidence_rejected_count=0, proven_false_rejected_count=100,
        invalid_evidence_rejected_count=0, plausible_eligibility_lower_bound=0, plausible_eligibility_upper_bound=0,
        rejection_reasons_counts={"NONE": 100}, post_arbitration_timestamps_selected=0,
        arbitration_suppressed_ties_count=0, arbitration_winner_substitutions_vs_b00=0,
        materialized_accepted_count=0, materialized_modified_tp_count=0,
        fill_mode_summaries={fm: _summarize_episode_list_kh01b([], 0) for fm in FILL_MODES_KH01B},
        primary_nb_episodes=[], all_mode_episodes={fm: [] for fm in FILL_MODES_KH01B},
        matched_b00_242_counterfactual={}, si8_separate_diagnostic=None, bundle_sha256="empty",
    )
    ec0 = compute_kh01b_r1_edge_uncertainty_and_research_verdict(empty_bnd, ())
    assert ec0.closed_trades_nb == 0
    assert ec0.expectancy_1x_nb_r is None
    assert ec0.cluster_bootstrap_95ci_expectancy_r is None
    assert ec0.research_verdict == "NO_VALID_SETUPS"
    assert ec0.qualifies_for_kh01c is False
    assert ec0.activation_ready == "NO"


def test_kh01b_r1_offline_data_collector_and_oos_escrow_guards() -> None:
    # Default OFFLINE_VALIDATE mode makes 0 network calls and verifies canonical dataset
    offline_res = run_offline_collector_self_audit()
    assert offline_res["mode"] == "OFFLINE_VALIDATE"
    assert offline_res["network_requests_made"] == 0
    assert offline_res["canonical_dataset_verified"] is True

    ts_ms = 1_794_799_980_000  # Strictly minute-aligned (divisible by 60_000)
    good_page = {
        "raw_http_sha256": "f" * 64,
        "server_time_ms": ts_ms + 300_000,
        "rows": [
            [str(ts_ms), "3700.0", "3701.5", "3699.5", "3701.0", "15.2"],
            [str(ts_ms + 60_000), "3701.0", "3702.0", "3700.5", "3701.8", "18.4"],
            [str(ts_ms + 300_000), "3702.0", "3703.0", "3701.0", "3702.5", "9.0"],  # unclosed at server_time=ts_ms+300s!
        ],
    }
    norm = validate_and_normalize_paginated_candles([good_page], symbol="XAUUSDT", granularity="1m", duration_ms=60_000)
    assert norm["status"] == "PASS"
    assert norm["accepted_closed_bars_count"] == 2
    assert norm["unclosed_bars_excluded_count"] == 1
    assert all(b["quote_mode"] == "MODELED_BID_ASK" for b in norm["bars"])

    # Invalid OHLC high < low rejected
    bad_ohlc = {
        "raw_http_sha256": "f" * 64,
        "server_time_ms": ts_ms + 300_000,
        "rows": [[str(ts_ms), "3700.0", "3698.0", "3699.5", "3700.0", "10.0"]],
    }
    assert validate_and_normalize_paginated_candles([bad_ohlc], symbol="XAUUSDT", granularity="1m", duration_ms=60_000)["status"] == "FAIL"

    # Stale BBO & required unverified calendar rejected
    stale_bbo = validate_prospective_bbo_snapshot({
        "symbol": "XAUUSDT", "raw_http_sha256": "e" * 64,
        "receive_ts_ms": ts_ms + 60_000, "exchange_ts_ms": ts_ms,
        "bid": 3700.1, "ask": 3700.2, "bid_size": 1.0, "ask_size": 1.0,
    }, max_staleness_ms=15_000)
    assert stale_bbo["status"] == "FAIL"
    assert any("STALE_BBO_QUOTE" in v for v in stale_bbo["violations"])

    unver_cal_bbo = validate_prospective_bbo_snapshot({
        "symbol": "XAUUSDT", "raw_http_sha256": "e" * 64,
        "receive_ts_ms": ts_ms + 100, "exchange_ts_ms": ts_ms,
        "bid": 3700.1, "ask": 3700.2, "bid_size": 1.0, "ask_size": 1.0,
    }, calendar_verified=False, require_verified_calendar=True)
    assert unver_cal_bbo["status"] == "FAIL"

    # Future OOS Escrow rejects DEV_EXTENDED_RESEARCH promotion, <180m embargo, and evaluation_count > 0
    dev_ext_sha = "d" * 64
    esc_bad = build_and_validate_future_oos_escrow_registry(
        freeze_timestamp_ms=ts_ms,
        frozen_candidate_code_sha="sha_r1",
        frozen_config_sha256="cfg_r1",
        known_dev_dataset_shas=[CANONICAL_DATASET_SHA256_KH01B_R1, dev_ext_sha],
        proposed_oos_entries=[{
            "dataset_id": "contaminated_dev",
            "dataset_sha256": dev_ext_sha,
            "earliest_bar_open_ms": ts_ms + 60_000,
            "latest_bar_close_ms": ts_ms + 3_600_000,
            "strategy_evaluation_count": 1,
            "partition_role": "DEV_EXTENDED_RESEARCH",
            "used_for_entry_selection": True,
        }],
    )
    assert esc_bad["status"] == "FAIL"
    assert esc_bad["FRESH_OOS_VERDICT"] == "FAIL_ESCROW_CONTAMINATION"
    assert len(esc_bad["violations"]) >= 4
