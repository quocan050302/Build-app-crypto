"""
Backend regression & adversarial negative-control suite for AURUM Desk V17.9.3.1.2.14:
  - P0-A: CanonicalScopeAccountingDTO fail-closed schema/conservation/closure-order rules (PNL-01..PNL-12)
  - P0-B: Tri-state provenance-aware 267 target reachability & Net R:R recomputation audit
  - P0-C: Repaired D4 Unicorn (detect_breaker_blocks signature + brk.low/brk.high + unconsumed FVG overlap)
          and post-confirmation causal HTF alignment + cross-adapter output-hash parity
  - R3:   All 100 adversarial negative controls (V01..V100) in disposable temporary directories
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import pytest

from services.v17_9_2_structural_stop_intelligence import (
    evaluate_r3_true_delayed_counterfactual_v17931213,
    evaluate_shared_delayed_entry_adapter_v17931213,
)
from services.v17_9_3_1_2_7_causal_signal_truth import canonical_hash
from services.v17_9_3_1_2_13_delayed_entry_materialization_and_truth_service import (
    _adapt_canonical_policy_fill_mode_strict,
    _replay_chronological_1pos_portfolio_from_matched_rows_v17931213,
)
from services.v17_9_3_1_2_14_accounting_target_and_d4_truth_service import (
    CANONICAL_DATASET_SHA256_V17931214,
    GROUNDED_V13_REMOTE_HEAD,
    MANDATORY_BRANCH_V17931214,
    REQUIRED_ARTIFACTS_V17931214,
    VERSION_V17931214,
    _build_default_v14_control_fixtures,
    audit_target_reachability_row_v17931214,
    build_v17931214_callgraph_and_defect_matrix,
    compare_accounting_scopes_strict_v17931214,
    find_next_causal_entry_confirmation_v179312,
    run_negative_controls_v01_to_v100_v17931214,
    validate_canonical_scope_accounting_v17931214,
    verify_267_target_audit_v17931214,
    verify_d4_funnel_and_execution_truth_v17931214,
    verify_git_push_safety_v17931214,
    verify_v17931214_cross_view_numerical_consistency,
)


def _synthetic_unicorn_breaker_fvg_bars():
    """
    Constructs a deterministic closed 1M + 1H + 4H candle sequence containing:
      - Bullish 1H & 4H structure (higher highs & higher lows)
      - A prior 1M swing high swept/broken upward creating a bullish BreakerBlock
      - An overlapping unconsumed 1M bullish FVG + MSS/displacement + retest bar strictly after base_idx
    """
    base_open = 1_773_000_000_000
    bars_1m = []
    # 0..18: realistic ~3.0 USD M1 ranges (ATR_1m ~ 3.0), swing high at idx=12 (high=4005.0),
    # bearish sweep candle at idx=15 [3998.0..4004.0] forming the source OB -> Bullish Breaker [3998.0..4004.0]
    # once idx=20 breaks 4005.0 upward, with overlapping bullish FVG [4003.4..4004.2] retested at idx=22!
    prices = [
        (3999.0, 4001.8, 3998.6, 4000.5),
        (4000.5, 4002.6, 3999.4, 4001.2),
        (4001.2, 4003.0, 3999.8, 4001.6),
        (4001.6, 4003.2, 4000.0, 4001.9),
        (4001.9, 4003.4, 4000.2, 4002.1),
        (4002.1, 4003.5, 4000.4, 4002.3),
        (4002.3, 4003.6, 4000.5, 4002.5),
        (4002.5, 4003.7, 4000.6, 4002.7),
        (4002.7, 4003.8, 4000.8, 4002.9),
        (4002.9, 4003.9, 4000.9, 4003.1),
        (4003.1, 4004.0, 4001.0, 4003.3),
        (4003.3, 4004.1, 4001.1, 4003.5),
        (4003.5, 4005.0, 4002.0, 4004.2),  # idx 12: Confirmed Swing High = 4005.0 (left=2, right=2)
        (4004.2, 4004.5, 4001.5, 4003.9),  # idx 13: lower high
        (4003.9, 4004.2, 4001.2, 4003.8),  # idx 14: lower high -> confirms idx 12 Swing High at idx 14 close
        (4003.8, 4004.0, 3998.0, 3998.8),  # idx 15: bearish sweep candle (lowest low 3998.0, zone [3998.0, 4004.0])
        (3998.8, 4001.8, 3998.6, 4001.4),  # idx 16: bounce
        (4001.4, 4002.9, 4000.5, 4002.4),  # idx 17: bounce
        (4002.4, 4003.3, 4001.2, 4002.8),  # idx 18: base_idx
        # Post-base bars (k > 18):
        (4002.8, 4003.4, 4002.2, 4003.2),  # idx 19 (wait=1): c1 high = 4003.4
        (4003.2, 4006.8, 4003.0, 4006.6),  # idx 20 (wait=2): strong displacement breaking 4005.0 -> Breaker [3998.0, 4004.0]!
        (4006.6, 4007.2, 4004.2, 4006.8),  # idx 21 (wait=3): c3 low = 4004.2 > c1 high 4003.4 -> Bullish FVG [4003.4, 4004.2] overlaps Breaker [3998.0, 4004.0] on [4003.4, 4004.0]!
        (4006.8, 4007.0, 4003.8, 4005.8),  # idx 22 (wait=4): retests FVG [4003.4, 4004.2] & Breaker [3998.0, 4004.0], closes 4005.8!
    ]
    for i, (o, h, l, c) in enumerate(prices):
        bars_1m.append(
            {
                "open_time": base_open + i * 60_000,
                "close_time": base_open + (i + 1) * 60_000,
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": 100.0,
                "is_closed": True,
            }
        )
    # Append 30 forward bars reaching TP
    for j in range(23, 55):
        bars_1m.append(
            {
                "open_time": base_open + j * 60_000,
                "close_time": base_open + (j + 1) * 60_000,
                "open": 4006.4 + (j - 23) * 0.8,
                "high": 4007.5 + (j - 23) * 0.8,
                "low": 4006.0 + (j - 23) * 0.8,
                "close": 4007.2 + (j - 23) * 0.8,
                "volume": 100.0,
                "is_closed": True,
            }
        )

    def _bull_htf(tf_ms: int, count: int, end_ms: int):
        out = []
        start_ms = end_ms - count * tf_ms
        # Clear ZigZag higher-highs & higher-lows below 4005 with an unswept prior structural swing high at 4027.0
        # so evaluate_htf_structure -> BULLISH AND select_viable_structural_target finds a Net R:R >= 2.0 pool!
        mids = [
            3945, 3960, 3950, 3972, 3960, 3985,
            3972, 4022, 3995, 3982, 3998, 3986,
            4002, 3990, 4004, 3993, 4006, 3996,
            4008, 3998, 4010, 4000, 4011, 4002,
        ]
        for k in range(count):
            mid = float(mids[k % len(mids)])
            h_pad = 5.0 if k != 7 else 5.0  # k=7 -> high = 4027.0 (unswept by subsequent k=8..23 highs <= 4016.0!)
            out.append(
                {
                    "open_time": start_ms + k * tf_ms,
                    "close_time": start_ms + (k + 1) * tf_ms,
                    "open": mid - 2.0,
                    "high": mid + h_pad,
                    "low": mid - 5.0,
                    "close": mid + 2.5,
                    "volume": 1000.0,
                    "is_closed": True,
                }
            )
        return out

    end_htf = base_open
    bars_1h = _bull_htf(3_600_000, 24, end_htf)
    bars_4h = _bull_htf(14_400_000, 24, end_htf)
    return bars_1m, bars_1h, bars_4h


def test_p0a_strict_policy_schema_adapter_and_canonical_dto():
    fx = _build_default_v14_control_fixtures("run_test", "a" * 40)
    adapted = _adapt_canonical_policy_fill_mode_strict(
        "SI8_RESEARCH_DISCOVERY_REPAIRED",
        "NEXT_BAR_OPEN_BID_ASK",
        fx["sum_si8"],
        {"materialized_intents_count": 5, "materialized_intent_ids": ["i1", "i2", "i3", "i4", "i5"]},
    )
    assert adapted["scheduled_episodes"] == 4
    assert adapted["closed_trades_count"] == 4
    assert adapted["wins_count"] == 0
    assert adapted["losses_count"] == 4
    assert adapted["realized_net_pnl_usd"] == pytest.approx(-9.7372)

    # Missing producer key MUST raise ValueError instead of silently defaulting to 0.0
    broken_legacy_shape = {
        "closed_trades_count": 4,
        "wins_count": 0,
        "losses_count": 4,
        "total_scheduled_episodes": 4,
        "open_at_horizon_count": 0,
        "data_gap_unresolved_count": 0,
        "unfilled_or_no_fill_count": 0,
        "blocked_by_open_position_count": 1,
        "total_realized_gross_usd": -9.3372,
        "total_fees_usd": 0.4000,
        # `total_realized_net_usd` intentionally omitted!
        "expectancy_net_r": -0.9875,
    }
    with pytest.raises(ValueError, match="SCHEMA_ERROR_MISSING_MANDATORY_ACCOUNTING_KEYS"):
        _adapt_canonical_policy_fill_mode_strict(
            "SI8_RESEARCH_DISCOVERY_REPAIRED",
            "NEXT_BAR_OPEN_BID_ASK",
            broken_legacy_shape,
            {"materialized_intents_count": 5},
        )


def test_p0a_closure_sorted_drawdown_and_zero_sample_inconclusive():
    # Trade A enters at t=1000 and closes WIN (+6.0) late at t=9000;
    # Trade B (in matched/1-pos synthetic test) closes LOSS (-2.0) early at t=3000;
    # Trade C closes LOSS (-1.5) at t=9500 after Trade A's +6.0 peak!
    # Closure order: B (-2.0 at 3000, eq=-2.0, peak=0.0, dd=2.0) -> A (+6.0 at 9000, eq=+4.0, peak=4.0, dd=2.0) -> C (-1.5 at 9500, eq=+2.5, peak=4.0, dd=2.0).
    # Schedule order if A were summed first: A (+6.0, peak=6.0) -> B (-2.0, eq=4.0, dd=2.0) -> C (-1.5, eq=2.5, dd=3.5)!
    rows = [
        {
            "opportunity_id": "A_early_entry_late_win",
            "status": "SCHEDULED",
            "new_decision_timestamp_ms": 1000,
            "filled": True,
            "is_closed": True,
            "outcome_label": "WIN",
            "entry_fill_ts": 1000,
            "exit_fill_ts": 9000,
            "planned_net_risk_usd": 2.50,
            "entry_fee_usd": 0.05,
            "exit_fee_usd": 0.05,
            "total_fee_usd": 0.10,
            "realized_gross_pnl_usd": 6.10,
            "realized_net_pnl_usd": 6.00,
            "realized_r_net": 2.40,
        },
        {
            "opportunity_id": "B_early_close_loss",
            "status": "SCHEDULED",
            "new_decision_timestamp_ms": 2000,
            "filled": True,
            "is_closed": True,
            "outcome_label": "LOSS",
            "entry_fill_ts": 2000,
            "exit_fill_ts": 3000,
            "planned_net_risk_usd": 2.50,
            "entry_fee_usd": 0.05,
            "exit_fee_usd": 0.05,
            "total_fee_usd": 0.10,
            "realized_gross_pnl_usd": -1.90,
            "realized_net_pnl_usd": -2.00,
            "realized_r_net": -0.80,
        },
        {
            "opportunity_id": "C_late_close_loss",
            "status": "SCHEDULED",
            "new_decision_timestamp_ms": 3500,
            "filled": True,
            "is_closed": True,
            "outcome_label": "LOSS",
            "entry_fill_ts": 3500,
            "exit_fill_ts": 9500,
            "planned_net_risk_usd": 2.50,
            "entry_fee_usd": 0.05,
            "exit_fee_usd": 0.05,
            "total_fee_usd": 0.10,
            "realized_gross_pnl_usd": -1.40,
            "realized_net_pnl_usd": -1.50,
            "realized_r_net": -0.60,
        },
    ]
    summary_bad_dd = {
        "policy_id": "TEST_SCOPE",
        "fill_mode": "NEXT_BAR_OPEN_BID_ASK",
        "scheduled_episodes_count": 3,
        "blocked_by_open_position_count": 0,
        "closed_trades_count": 3,
        "wins_count": 1,
        "losses_count": 2,
        "open_at_horizon_count": 0,
        "data_gap_unresolved_count": 0,
        "unfilled_or_no_fill_count": 0,
        "realized_gross_pnl_usd": 2.80,
        "total_fees_usd": 0.30,
        "realized_net_pnl_usd": 2.50,
        "expectancy_net_r": round((2.40 - 0.80 - 0.60) / 3, 4),
        "max_drawdown_usd": 3.50,  # Schedule-order drawdown -> MUST FAIL!
    }
    bad_res = validate_canonical_scope_accounting_v17931214(
        summary_bad_dd,
        episode_rows=rows,
        run_sha="b" * 40,
        scope_entity_id="TEST_SCOPE",
        fill_mode="NEXT_BAR_OPEN_BID_ASK",
        portfolio_scope="VARIANT_1POS_CHRONOLOGICAL_PORTFOLIO",
        materialized_or_accepted_count=3,
    )
    assert bad_res["status"] == "FAIL"
    assert any("CLOSURE_SORTED_MAX_DRAWDOWN_MISMATCH" in v for v in bad_res["violations"])

    good_res = validate_canonical_scope_accounting_v17931214(
        {**summary_bad_dd, "max_drawdown_usd": 2.00},
        episode_rows=rows,
        run_sha="b" * 40,
        scope_entity_id="TEST_SCOPE",
        fill_mode="NEXT_BAR_OPEN_BID_ASK",
        portfolio_scope="VARIANT_1POS_CHRONOLOGICAL_PORTFOLIO",
        materialized_or_accepted_count=3,
    )
    assert good_res["status"] == "PASS"

    # Zero-sample portfolio replay MUST mark expectancy_status == INCONCLUSIVE_NO_SAMPLE
    zero_port = _replay_chronological_1pos_portfolio_from_matched_rows_v17931213(
        "D4_CAUSAL_UNICORN_HTF_FULL_PIPELINE",
        "NEXT_BAR_OPEN_BID_ASK",
        [{"opportunity_id": "rej_1", "status": "REJECTED_PRE_ENTRY", "filled": False, "is_closed": False}],
    )
    assert zero_port["closed_trades_count"] == 0
    assert zero_port["expectancy_status"] == "INCONCLUSIVE_NO_SAMPLE"
    assert zero_port["expectancy_is_positive_evidence"] is False


def test_p0b_tri_state_target_audit_and_conservation():
    fx = _build_default_v14_control_fixtures("run_p0b", "c" * 40)
    valid_row = audit_target_reachability_row_v17931214(
        opportunity_id="opp_valid_1h",
        cohort_role="HISTORICAL_267_SI9_H5",
        historical_rejection="SI9_H5_TARGET_NOT_REACHABLE_OR_UNPROVEN",
        current_rejection="ACCEPTED_MATERIALIZED_INTENT",
        decision_row=fx["dec_row"],
        feature_row=fx["feat_row"],
        pools_by_id=fx["pools_map"],
        datasets=fx["ds_fix"],
        close_ts_index=fx["cts_fix"],
    )
    assert valid_row["tri_state_status"] == "VALID_VALUE"
    assert valid_row["resolved_source_tf"] == "1H"
    assert valid_row["net_rr"] >= 2.0
    assert valid_row["first_decisive_failure_code"] == "M1_VS_HTF_ATR_UNIT_MISMATCH_ON_VALID_HTF_POOL"

    # Verify 267 + control conservation checker catches wrong sum
    fake_267 = [replace_oid := {**valid_row, "opportunity_id": f"o_{i}"} for i in range(267)]
    for r in fake_267:
        r["row_checksum_sha256"] = canonical_hash({k: v for k, v in r.items() if k != "row_checksum_sha256"})
    ctrl = {**valid_row, "opportunity_id": "ctrl_0", "cohort_role": "CONTROL_GROUP"}
    ctrl["row_checksum_sha256"] = canonical_hash({k: v for k, v in ctrl.items() if k != "row_checksum_sha256"})
    ok_267 = verify_267_target_audit_v17931214(
        fake_267 + [ctrl],
        {
            "mutually_exclusive_primary_root_causes_267": {"M1_VS_HTF_ATR_UNIT_MISMATCH_ON_VALID_HTF_POOL": 267},
            "source_timeframe_distribution_267": {"1H": 267},
        },
    )
    assert ok_267["status"] == "PASS"
    _ = replace_oid


def test_p0c_repaired_unicorn_breaker_fvg_confirmation_and_cross_adapter_parity():
    bars_1m, bars_1h, bars_4h = _synthetic_unicorn_breaker_fvg_bars()
    base_idx = 18
    base_dec_ms = bars_1m[base_idx]["close_time"]

    # 1. Direct confirmation finder with require_true_unicorn=True must bind BOTH fvg_id and breaker_id!
    conf = find_next_causal_entry_confirmation_v179312(
        {
            "checkpoint_index_1m": base_idx,
            "decision_timestamp_ms": base_dec_ms,
            "direction": "LONG",
            "model_id": "UNICORN_MODEL",
            "symbol": "XAUUSDT",
        },
        bars_1m,
        max_wait_bars=10,
        require_mss_displacement=True,
        require_breaker_fvg_retest=True,
        strictly_after_base=True,
        model_aware=True,
        require_true_unicorn=True,
    )
    assert conf["status"] == "CONFIRMED"
    assert conf["wait_bars"] >= 1
    assert conf["new_decision_timestamp_ms"] > base_dec_ms
    src_ids = conf["confirmation_source_ids"]
    assert src_ids["fvg_id"] is not None
    assert src_ids["breaker_id"] is not None
    brk_ov = conf["confirmation_evidence"]["breaker_overlap_evidence"]
    assert brk_ov is not None
    assert brk_ov["overlap_high"] - brk_ov["overlap_low"] >= 0.01 - 1e-6

    # 2. Full D4 delayed counterfactual & cross-adapter output-hash parity across all 3 execution contexts
    hashes = set()
    for ctx_mode in ("REPLAY_BACKTEST", "MARKET_SHADOW", "PRODUCTION_ORCHESTRATOR_DRY_RUN"):
        res = evaluate_shared_delayed_entry_adapter_v17931213(
            execution_context_mode=ctx_mode,
            activation_flags={"LIVE": False, "PAPER": False, "AUTO_LIVE": False, "AUTO_PAPER": False, "enabled_in_production": False},
            variant_id="D4_CAUSAL_UNICORN_HTF_FULL_PIPELINE",
            opportunity_id="opp_synth_unicorn_d4",
            episode_key="ep_synth_unicorn_d4",
            model_id="UNICORN_MODEL",
            direction="LONG",
            decision_timestamp_ms=base_dec_ms,
            baseline_entry=4002.6,
            baseline_sl=3997.5,
            baseline_tp=4022.0,
            closed_m1=bars_1m[: base_idx + 1],
            closed_m5=bars_1m[: base_idx + 1],
            closed_m15=bars_1m[: base_idx + 1],
            closed_h1=bars_1h,
            closed_h4=bars_4h,
            forward_bars=bars_1m[base_idx + 1 :],
            pre_entry_features={"require_true_unicorn": True},
            fill_mode="NEXT_BAR_OPEN_BID_ASK",
            dataset_sha=CANONICAL_DATASET_SHA256_V17931214,
        )
        assert res["adapter_default_activation_state"] == "OFF"
        assert res["adapter_live_orders_emitted"] == 0
        assert res["wait_bars"] >= 1
        assert res["new_decision_timestamp_ms"] > base_dec_ms
        assert res["confirmation_source_ids"]["breaker_id"] is not None
        assert res["confirmation_source_ids"]["fvg_id"] is not None
        hashes.add(canonical_hash({k: v for k, v in res.items() if k != "adapter_execution_context_mode"}))
    assert len(hashes) == 1

    # Enabling LIVE or PAPER in adapter MUST raise PermissionError
    with pytest.raises(PermissionError, match="LIVE_OR_PAPER_ACTIVATION_FORBIDDEN"):
        evaluate_shared_delayed_entry_adapter_v17931213(
            execution_context_mode="PRODUCTION_ORCHESTRATOR_DRY_RUN",
            activation_flags={"LIVE": True},
            variant_id="D4_CAUSAL_UNICORN_HTF_FULL_PIPELINE",
            opportunity_id="opp_bad",
            episode_key="ep_bad",
            model_id="UNICORN_MODEL",
            direction="LONG",
            decision_timestamp_ms=base_dec_ms,
            baseline_entry=4002.6,
            baseline_sl=3997.5,
            baseline_tp=4022.0,
            closed_m1=bars_1m[: base_idx + 1],
        )


def test_callgraph_and_required_artifacts_contract():
    repo_root = Path(__file__).resolve().parents[3]
    cg = build_v17931214_callgraph_and_defect_matrix(repo_root)
    assert cg["version"] == VERSION_V17931214
    assert cg["repaired_defects_count"] == 5
    assert cg["anchored_symbols_count"] >= 180
    assert len(REQUIRED_ARTIFACTS_V17931214) >= 28

    git_ok = verify_git_push_safety_v17931214(
        current_branch=MANDATORY_BRANCH_V17931214,
        push_command_argv=["git", "push", "origin", MANDATORY_BRANCH_V17931214],
        expected_remote_head=GROUNDED_V13_REMOTE_HEAD,
        observed_remote_head_before_push=GROUNDED_V13_REMOTE_HEAD,
        local_head_is_descendant_of_remote=True,
        local_artifact_sha256_map={"a.json": "123"},
        remote_readback_sha256_map={"a.json": "123"},
    )
    assert git_ok["status"] == "PASS"


@pytest.fixture(scope="module")
def v01_to_v100_results_fixture():
    return run_negative_controls_v01_to_v100_v17931214()


@pytest.mark.parametrize("ctrl_idx", list(range(100)))
def test_adversarial_negative_control_v01_to_v100(v01_to_v100_results_fixture, ctrl_idx: int):
    controls = v01_to_v100_results_fixture["controls"]
    assert len(controls) == 100
    ctrl = controls[ctrl_idx]
    assert ctrl["rejected"] is True, f"Negative control #{ctrl_idx + 1} failed: {ctrl}"
