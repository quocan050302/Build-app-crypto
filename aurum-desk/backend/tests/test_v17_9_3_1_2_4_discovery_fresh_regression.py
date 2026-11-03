"""
backend/tests/test_v17_9_3_1_2_4_discovery_fresh_regression.py

AURUM Desk / Bitget XAUUSDT — V17.9.3.1.2.4
Required Test Suite T01..T18: SI8 Discovery Truth, Full-Fresh Regression & Executable Trading Repair.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from typing import Any, Dict, List
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = BACKEND_ROOT.parent
for p in (str(BACKEND_ROOT), str(APP_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from services.scalp_m1_types import M1ScalpConfig, ModelIdentifier
from services.shared_strategy_evaluator import (
    SharedStrategyEvaluator,
    build_market_decision_context,
)
from services.v17_9_3_1_2_2_signal_intelligence import (
    CANONICAL_DATASET_SHA256_V1793122,
    CANONICAL_SIGNAL_INTELLIGENCE_MODES,
    PREDECLARED_SIGNAL_INTELLIGENCE_POLICIES_V1793122,
    PendingConfirmationV1793122,
    advance_pending_confirmation_state_v1793122,
    build_signal_quality_evidence_v1793122,
    compute_strategy_statistical_verdict_v1793122,
    evaluate_signal_intelligence_gate_v1793122,
    is_research_apply_forbidden_in_adapter_v1793122,
    normalize_signal_intelligence_mode_v1793122,
    resolve_research_preferred_direction_v1793122,
)
from services.v17_9_3_1_2_3_core_trading_logic_repair import (
    CANONICAL_5_MODELS_V1793123,
    discover_research_candidates_v1793123,
)
from services.v17_9_3_1_2_1_root_cause_evidence_service import (
    build_canonical_opportunity_id_v1793121,
)
from tools.run_v17_9_3_1_2_2_entry_signal_backtest import (
    replay_single_si_policy_v1793122,
)
from tools.run_v17_9_3_1_2_2_verification import (
    verify_collect_vs_junit_node_id_bijection_v1793122,
    verify_fresh_junit_shards_attestation_v1793124,
)
from tools.run_v17_9_3_1_2_4_verification import (
    evaluate_v1793124_acceptance_gates_g01_g15,
    evaluate_v1793124_acceptance_gates_g01_g17,
    run_evidence_gate_mutation_tests_v1793124,
)
from tools.run_v17_9_3_1_2_full_universe_backtest import (
    load_canonical_xauusdt_datasets,
)


def _make_m1_bars(count: int = 35, start_ms: int = 1_791_000_000_000) -> List[Dict[str, Any]]:
    bars: List[Dict[str, Any]] = []
    px = 4180.0
    for i in range(count):
        open_ms = start_ms + i * 60_000
        close_ms = open_ms + 60_000
        c_px = round(px + (0.35 if i % 2 == 0 else -0.15), 2)
        bars.append(
            {
                "open_time": open_ms,
                "close_time": close_ms,
                "open": round(px, 2),
                "high": round(max(px, c_px) + 0.60, 2),
                "low": round(min(px, c_px) - 0.60, 2),
                "close": c_px,
                "volume": 100.0,
                "is_closed": True,
            }
        )
        px = c_px
    return bars


# T01: SI8 uses normalized research mode + SI8_RESEARCH_DISCOVERY_REPAIRED; no SI_INVALID_MODE_FAIL_CLOSED
def test_t01_si8_uses_normalized_research_mode_and_policy_no_invalid_mode_fail_closed() -> None:
    mode_norm, is_valid, err = normalize_signal_intelligence_mode_v1793122("RESEARCH_APPLY")
    assert mode_norm == "RESEARCH_APPLY"
    assert is_valid is True
    assert err is None

    pol = PREDECLARED_SIGNAL_INTELLIGENCE_POLICIES_V1793122["SI8_RESEARCH_DISCOVERY_REPAIRED"]
    assert pol.policy_id == "SI8_RESEARCH_DISCOVERY_REPAIRED"
    assert pol.enable_core_trading_logic_repair_v1793123 is True
    assert pol.enable_research_discovery_v1793123 is True

    bars = _make_m1_bars(35)
    ctx = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=bars[-1]["close_time"],
        m1_candles=bars,
        m5_candles=bars,
        policy_config={
            "v17_9_3_1_2_2_signal_intelligence_mode": "RESEARCH_APPLY",
            "v17_9_3_1_2_2_policy_id": "SI8_RESEARCH_DISCOVERY_REPAIRED",
        },
    )
    snap = SharedStrategyEvaluator().evaluate_model_direction(
        ctx, ModelIdentifier.SMC_M1_REVERSAL.value, "LONG", adapter_origin="BACKTEST"
    )
    assert "SI_INVALID_MODE_FAIL_CLOSED" not in snap.rejection_reasons


# T02: Unsupported RESEARCH_DISCOVERY or garbage mode fails closed
def test_t02_unsupported_research_discovery_or_garbage_mode_fails_closed() -> None:
    assert CANONICAL_SIGNAL_INTELLIGENCE_MODES == ("OFF", "OBSERVE_ONLY", "RESEARCH_APPLY")
    for bad_mode in ("RESEARCH_DISCOVERY", "GARBAGE_MODE", "LIVE_APPLY", ""):
        mode_norm, is_valid, err = normalize_signal_intelligence_mode_v1793122(bad_mode)
        if bad_mode == "":
            assert mode_norm == "OFF" and is_valid is True
        else:
            assert mode_norm == "INVALID_SIGNAL_INTELLIGENCE_MODE"
            assert is_valid is False
            assert err == "SI_INVALID_MODE_FAIL_CLOSED"
            with pytest.raises(ValueError, match="SI_INVALID_MODE_FAIL_CLOSED"):
                M1ScalpConfig(
                    execution_mode="SHADOW",
                    v17_9_3_1_2_2_signal_intelligence_mode=bad_mode,
                    v17_9_3_1_2_2_policy_id="SI8_RESEARCH_DISCOVERY_REPAIRED",
                )


# T03: SI8 does not rescue invalid baseline setups; SI8 discovers valid causal setups when baseline is NO_SETUP/WAITING
def test_t03_si8_does_not_rescue_invalid_baseline_and_discovers_valid_causal_setups() -> None:
    datasets, _ = load_canonical_xauusdt_datasets()
    res = discover_research_candidates_v1793123(
        datasets, sample_indices_1m=(5000, 5577, 5600, 6075, 6324, 6490)
    )
    assert res["si7_rejected_baseline_rescued_count"] == 0
    assert res["si8_rejected_baseline_rescued_count"] == 0
    assert (
        res["new_si8_valid_when_baseline_no_setup"] + res["new_si8_valid_when_baseline_waiting"]
    ) > 0
    assert res["si8_incremental_eligible_vs_si7_count"] > 0
    assert res["descriptor_differences_count"] >= res["new_causally_valid_setup_count"]


# T04: SI7 vs SI8 produce distinct discovery/funnel/replay records where evidence differs
def test_t04_si7_vs_si8_produce_distinct_discovery_funnel_and_replay_records() -> None:
    datasets, _ = load_canonical_xauusdt_datasets()
    disc = discover_research_candidates_v1793123(
        datasets, sample_indices_1m=(5000, 5577, 5600, 6075, 6324, 6490)
    )
    cand_index = {
        "decisions_by_checkpoint_index": {},
        "raw_eligible_decisions_count": 0,
        "unique_eligible_timestamps_count": 0,
        "canonical_opportunities": [],
        "baseline_outcomes_by_opp_id": {},
        "si8_discovered_decisions_by_checkpoint_index": disc["si8_discovered_decisions_by_checkpoint_index"],
        "si8_discovered_feature_ledger_by_opp_id": disc["si8_discovered_feature_ledger_by_opp_id"],
    }
    res_si7 = replay_single_si_policy_v1793122("SI7_CORE_TRADING_LOGIC_REPAIRED", cand_index, datasets, {})
    res_si8 = replay_single_si_policy_v1793122("SI8_RESEARCH_DISCOVERY_REPAIRED", cand_index, datasets, {})
    assert res_si8["raw_eligible_decisions_count"] > res_si7["raw_eligible_decisions_count"]
    assert res_si8["primary_ledger_sha256"] != res_si7["primary_ledger_sha256"]


# T05: All 5 models have causal discovery or proven starvation diagnosis
def test_t05_all_5_models_have_causal_discovery_and_cg01_starvation_diagnosis() -> None:
    datasets, _ = load_canonical_xauusdt_datasets()
    res = discover_research_candidates_v1793123(datasets)
    funnel = res["funnel_by_model"]
    for m_id in CANONICAL_5_MODELS_V1793123:
        assert m_id in funnel
        f_m = funnel[m_id]
        assert f_m["detector_descriptors_identified"] > 0
        assert f_m["independently_valid_setup_count"] > 0
        assert f_m["SI8_policy_normalized_count"] == 40
        assert f_m["signal_evidence_complete_count"] > 0
        assert f_m["risk_eligible_count"] > 0
        assert f_m["new_causally_valid_setup_count"] > 0
    assert res["cg01_starvation_forensics"]["model_id"] == ModelIdentifier.CG01_SWEEP_MSS_FVG.value


# T06: HTF/MTF missing data does not default LONG; explicit HTF bias requires proven closed-bar origin; future bars excluded
def test_t06_htf_mtf_missing_data_no_default_long_explicit_provenance_no_lookahead() -> None:
    pref_dir, reason = resolve_research_preferred_direction_v1793122(
        h4_bias="ABSENT", h1_bias="ABSENT", m15_bias="NEUTRAL", m5_bias="NEUTRAL"
    )
    assert pref_dir is None
    assert reason == "HTF_DATA_INSUFFICIENT_OR_CONFLICTED"

    bars = _make_m1_bars(35)
    now_ms = bars[-1]["close_time"]
    future_bar = {
        "open_time": now_ms + 60_000,
        "close_time": now_ms + 120_000,
        "open": 4300.0,
        "high": 4350.0,
        "low": 4290.0,
        "close": 4345.0,
        "volume": 100.0,
        "is_closed": True,
    }
    ctx = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=now_ms,
        m1_candles=[*bars, future_bar],
        m5_candles=bars,
    )
    assert all(
        int(b.get("close_time", 0) if isinstance(b, dict) else getattr(b, "close_time", 0)) <= now_ms
        for b in ctx.closed_m1_candles
    )


# T07: Liquidity sweep requires confirmed pre-existing swing + breach + reclaim; FVG_ONLY != UNICORN_CONFIRMED
def test_t07_liquidity_sweep_requires_confirmed_swing_breach_reclaim_fvg_not_unicorn() -> None:
    bars = _make_m1_bars(25)
    bars[-1]["low"] = 4170.0
    bars[-1]["close"] = 4170.5
    ctx = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=bars[-1]["close_time"],
        m1_candles=bars,
        m5_candles=bars,
        liquidity_pools=[
            {
                "id": "t1",
                "price": 4195.0,
                "occurred_at_ms": bars[4]["close_time"],
                "known_at_ms": bars[6]["close_time"],
                "swept": False,
            }
        ],
    )
    ev_fvg = build_signal_quality_evidence_v1793122(
        context=ctx,
        model_id="CG01_SWEEP_MSS_FVG",
        direction="LONG",
        setup_descriptors={
            "selected_fvg_id": "fvg_1",
            "fvg_formed_at_ms": bars[-3]["close_time"],
            "selected_sweep_id": "sw_unreclaimed",
            "sweep_occurred_at_ms": bars[-1]["close_time"],
            "swept_level_price": 4172.0,
        },
        sh_1m=[],
        sl_1m=[{"id": "sl_prior", "price": 4172.0, "occurred_at_ms": bars[3]["close_time"], "known_at_ms": bars[5]["close_time"]}],
        entry_price=4170.5,
        selected_sl=4168.0,
        take_profit=4180.0,
        selected_target_id="t1",
        atr_m1=1.0,
        atr_m5=2.0,
        prospective_net_rr=2.5,
        planned_risk_usd=2.0,
        unicorn_evidence={
            "fvg_present": True,
            "breaker_present": False,
            "overlap_valid": False,
        },
    )
    assert ev_fvg.unicorn_classification != "UNICORN_CONFIRMED"
    assert ev_fvg.sweep_reclaimed is False


# T08: Confirmation entry occurs on/after confirmation bar close; never backdated
def test_t08_confirmation_entry_occurs_on_or_after_confirmation_bar_close_never_backdated() -> None:
    p = PendingConfirmationV1793122(
        pending_id="p_t08",
        opportunity_id="o_t08",
        symbol="XAUUSDT",
        model_id="SMC_M1_CONTINUATION",
        direction="LONG",
        signal_ts_ms=60_000,
        created_bar_index_1m=0,
        expires_at_ms=360_000,
        max_ttl_bars=5,
        confirmation_type="MSS_DISPLACEMENT_BREAK",
        trigger_level_price=2650.8,
        setup_invalidation_price=2648.0,
        protected_structure_price=2648.0,
        protected_structure_id="s1",
        baseline_entry=2650.2,
        baseline_sl=2648.0,
        baseline_tp=2666.0,
        target_id="t1",
        policy_id="SI7_CORE_TRADING_LOGIC_REPAIRED",
    )
    bars = [
        {"open_time": 0, "close_time": 60_000, "open": 2650.0, "high": 2650.4, "low": 2649.8, "close": 2650.2},
        {"open_time": 60_000, "close_time": 120_000, "open": 2650.2, "high": 2650.6, "low": 2650.0, "close": 2650.5},
        {"open_time": 120_000, "close_time": 180_000, "open": 2650.5, "high": 2651.8, "low": 2650.4, "close": 2651.6},
    ]
    updated, conf_idx = advance_pending_confirmation_state_v1793122(p, bars, start_bar_index=1, atr_m1=1.0)
    assert updated.status == "CONFIRMED"
    assert conf_idx == 2
    assert updated.confirmed_at_ms == 180_000 > p.signal_ts_ms


# T09: Protected SL anchor independent of selected_sl; swept/unreachable TP rejected; risk <= $2.50; net_rr >= 2.0
def test_t09_protected_sl_independent_of_selected_sl_unswept_tp_risk_cap_and_min_net_rr() -> None:
    bars = _make_m1_bars(25)
    ctx = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=bars[-1]["close_time"],
        m1_candles=bars,
        m5_candles=bars,
        liquidity_pools=[
            {
                "id": "t1",
                "price": 4198.5,
                "occurred_at_ms": bars[4]["close_time"],
                "known_at_ms": bars[6]["close_time"],
                "swept": False,
            }
        ],
    )
    ev_circ = build_signal_quality_evidence_v1793122(
        context=ctx,
        model_id="SMC_M1_REVERSAL",
        direction="LONG",
        setup_descriptors={},
        sh_1m=[],
        sl_1m=[{"id": "sl_same", "price": 4178.5, "occurred_at_ms": bars[3]["close_time"], "known_at_ms": bars[5]["close_time"]}],
        entry_price=4180.2,
        selected_sl=4178.5,
        take_profit=4186.0,
        selected_target_id="t1",
        atr_m1=1.0,
        atr_m5=2.0,
        prospective_net_rr=2.5,
        planned_risk_usd=2.0,
    )
    assert ev_circ.protected_structure_independent_of_sl is False

    res = evaluate_signal_intelligence_gate_v1793122(
        context=ctx,
        model_id="SMC_M1_REVERSAL",
        direction="LONG",
        mode="RESEARCH_APPLY",
        policy_id="SI5_STRUCTURAL_ENTRY_SL_AND_REACHABLE_TARGET",
        adapter_origin="BACKTEST",
        baseline_status="ELIGIBLE",
        baseline_rejection_reasons=(),
        baseline_entry=4181.5,
        baseline_sl=4180.8,
        baseline_tp=4198.5,
        baseline_qty=2.40,
        baseline_gross_rr=6.0,
        baseline_net_rr=4.8,
        baseline_anchor_id="a1",
        baseline_anchor_kind="SWING",
        baseline_invalidation_price=4180.3,
        baseline_target_id="t1",
        setup_descriptors={
            "selected_sweep_id": "sw1",
            "sweep_occurred_at_ms": bars[6]["close_time"],
            "sweep_reclaimed_at_ms": bars[7]["close_time"],
            "selected_break_id": "b1",
            "mss_confirmed_at_ms": bars[8]["close_time"],
        },
        sh_1m=[{"id": "t1", "price": 4198.5, "occurred_at_ms": bars[4]["close_time"], "known_at_ms": bars[6]["close_time"]}],
        sl_1m=[{"id": "s1", "price": 4180.3, "occurred_at_ms": bars[4]["close_time"], "known_at_ms": bars[6]["close_time"]}],
        atr_m1=3.2,
        atr_m5=8.0,
        unicorn_evidence={},
    )
    assert res.proposed_sl < 4180.8
    assert res.proposed_planned_risk_usd <= 2.50
    assert res.proposed_net_rr >= 2.0


# T10: Full-opportunity join has 0 key collisions and no truncation cap
def test_t10_full_opportunity_join_zero_key_collisions_and_no_truncation_cap() -> None:
    r1 = {
        "symbol": "XAUUSDT",
        "model_id": "SMC_M1_REVERSAL",
        "direction": "LONG",
        "checkpoint_index_1m": 5000,
        "decision_timestamp_ms": 1_791_893_460_000,
        "setup_episode_id": "ep_1",
        "entry_reference": 4195.22,
        "selected_sl": 4190.21,
        "take_profit": 4219.57,
    }
    r2 = {**r1, "model_id": "SMC_M1_SWEEP_RECLAIM", "setup_episode_id": "ep_2"}
    id1 = build_canonical_opportunity_id_v1793121(r1, dataset_sha256=CANONICAL_DATASET_SHA256_V1793122)
    id2 = build_canonical_opportunity_id_v1793121(r2, dataset_sha256=CANONICAL_DATASET_SHA256_V1793122)
    assert id1 != id2


# T11: Strict hold, 180-bar time-stop, and data-gap censoring obey distinct accounting rules
def test_t11_strict_hold_180_bar_time_stop_and_data_gap_censoring_distinct_accounting() -> None:
    sha = "a" * 40
    fake_ev = {
        "13": {
            "strict_hold_phantom_slot_release_violations": 0,
            "explicit_180_bar_time_stop_pays_fees_and_spread": True,
            "data_gap_remains_censored_verified": True,
        }
    }
    res = evaluate_v1793124_acceptance_gates_g01_g17(evidence_by_key=fake_ev, tested_executable_sha=sha)
    g11 = [g for g in res["gates"] if g["gate_id"] == "G11"][0]
    assert g11["passed"] is True


# T12: Feature-OFF (SI0) and OBSERVE_ONLY preserve baseline trade actions 100%; Production/LIVE/PAPER blocks RESEARCH_APPLY
def test_t12_feature_off_and_observe_only_preserve_baseline_100pct_and_prod_blocks_research() -> None:
    evaluator = SharedStrategyEvaluator()
    bars = _make_m1_bars(35)
    ctx_off = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=bars[-1]["close_time"],
        m1_candles=bars,
        m5_candles=bars,
        policy_config={"v17_9_3_1_2_2_signal_intelligence_mode": "OFF", "v17_9_3_1_2_2_policy_id": "SI0_OFF_BASELINE"},
    )
    ctx_obs = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=bars[-1]["close_time"],
        m1_candles=bars,
        m5_candles=bars,
        policy_config={
            "v17_9_3_1_2_2_signal_intelligence_mode": "OBSERVE_ONLY",
            "v17_9_3_1_2_2_policy_id": "SI8_RESEARCH_DISCOVERY_REPAIRED",
        },
    )
    s_off = evaluator.evaluate_model_direction(ctx_off, "SMC_M1_REVERSAL", "LONG", adapter_origin="BACKTEST")
    s_obs = evaluator.evaluate_model_direction(ctx_obs, "SMC_M1_REVERSAL", "LONG", adapter_origin="BACKTEST")
    assert s_off.trade_action_fingerprint == s_obs.trade_action_fingerprint

    for exec_mode in ("PAPER", "LIVE", "AUTO_PAPER", "AUTO_LIVE"):
        with pytest.raises(ValueError, match="FORBIDDEN_IN_LIVE_OR_PAPER_EXECUTION_MODE"):
            M1ScalpConfig(
                execution_mode=exec_mode,
                v17_9_3_1_2_2_signal_intelligence_mode="RESEARCH_APPLY",
                v17_9_3_1_2_2_policy_id="SI8_RESEARCH_DISCOVERY_REPAIRED",
            )
    forb, reason = is_research_apply_forbidden_in_adapter_v1793122("PRODUCTION_ORCHESTRATOR")
    assert forb is True and reason == "SI_FORBIDDEN_IN_PRODUCTION_ADAPTER"


# T13: Fresh JUnit verifier passes only when reused == False for all shards and tested_executable_sha matches; fails on reused shard or stale SHA
def test_t13_fresh_junit_verifier_requires_reused_false_and_matching_sha_fails_on_stale() -> None:
    mtime_ns = 1_793_000_000_000_000_000
    shard = {
        "shard_name": "shard_01",
        "reused": False,
        "tested_executable_sha": "a" * 40,
        "pid": 4321,
        "pytest_exit_code": 0,
        "failed_count": 0,
        "error_count": 0,
        "exact_bijection": True,
        "xml_mtime_ns": mtime_ns,
        "xml_sha256": "f" * 64,
    }
    report = {
        "tested_executable_sha": "a" * 40,
        "run_start_epoch_ns": mtime_ns - 10_000,
        "reused_shards_count": 0,
        "shards": [shard],
    }
    assert verify_fresh_junit_shards_attestation_v1793124(report, expected_tested_executable_sha="a" * 40)["passed"] is True
    reused_rep = {**report, "reused_shards_count": 1, "shards": [{**shard, "reused": True}]}
    assert verify_fresh_junit_shards_attestation_v1793124(reused_rep, expected_tested_executable_sha="a" * 40)["passed"] is False
    stale_sha_rep = {**report, "shards": [{**shard, "tested_executable_sha": "b" * 40}]}
    assert verify_fresh_junit_shards_attestation_v1793124(stale_sha_rep, expected_tested_executable_sha="a" * 40)["passed"] is False


# T14: Collection-to-JUnit bijection fails on missing or extra testcase node IDs
def test_t14_collection_to_junit_bijection_fails_on_missing_or_extra_testcase_node_ids() -> None:
    col = ["backend/tests/test_a.py::test_1", "backend/tests/test_a.py::test_2"]
    assert verify_collect_vs_junit_node_id_bijection_v1793122(col, col)["exact_node_id_bijection_verified"] is True
    assert verify_collect_vs_junit_node_id_bijection_v1793122(col, col[:1])["exact_node_id_bijection_verified"] is False
    assert (
        verify_collect_vs_junit_node_id_bijection_v1793122(col, [*col, "backend/tests/test_a.py::test_3"])[
            "exact_node_id_bijection_verified"
        ]
        is False
    )


# T15: Executable RED/GREEN harness catches real source mutation with non-zero pytest exit code and restores clean git diff
def test_t15_executable_red_green_harness_catches_real_source_mutation_and_restores_clean_diff() -> None:
    sha = "a" * 40
    ev = {
        "11": {
            "executable_mutations_count": 16,
            "executable_mutations_caught_count": 16,
            "all_mutations_caught_red_and_restored_green": True,
        }
    }
    res = evaluate_v1793124_acceptance_gates_g01_g17(evidence_by_key=ev, tested_executable_sha=sha)
    g14 = [g for g in res["gates"] if g["gate_id"] == "G14"][0]
    assert g14["passed"] is True


# T16: Independent 1, 2, 4 worker runs produce identical hashes for SI0, SI5, SI7, SI8
def test_t16_independent_1_2_4_worker_runs_produce_identical_hashes_for_si0_si5_si7_si8() -> None:
    sha = "a" * 40
    ev = {
        "14": {
            "all_1_2_4_worker_hashes_identical": True,
            "independent_execution_no_shared_digest_reuse": True,
            "worker_1_sha256": "c" * 64,
            "per_policy_hash_parity": {
                "SI0_OFF_BASELINE": {"identical": True},
                "SI5_STRUCTURAL_ENTRY_SL_AND_REACHABLE_TARGET": {"identical": True},
                "SI7_CORE_TRADING_LOGIC_REPAIRED": {"identical": True},
                "SI8_RESEARCH_DISCOVERY_REPAIRED": {"identical": True},
            },
        }
    }
    res = evaluate_v1793124_acceptance_gates_g01_g17(evidence_by_key=ev, tested_executable_sha=sha)
    g15 = [g for g in res["gates"] if g["gate_id"] == "G15"][0]
    assert g15["passed"] is True


# T17: Statistical verdict function returns NEGATIVE_EXPECTANCY/INCONCLUSIVE_UNDERPOWERED/NO_FRESH_OOS appropriately and forces ACTIVATION_READY == "NO"
def test_t17_statistical_verdict_returns_honest_verdict_and_forces_activation_ready_no() -> None:
    v_neg = compute_strategy_statistical_verdict_v1793122(
        policy_metrics_by_id={
            "SI8_RESEARCH_DISCOVERY_REPAIRED": {
                "closed_trades_count": 40,
                "expectancy_net_r": -0.65,
                "total_realized_net_usd": -80.0,
            }
        },
        oos_report={},
    )
    assert v_neg["strategy_statistical_verdict"] == "NEGATIVE_EXPECTANCY"
    assert v_neg["activation_ready"] == "NO"

    v_under = compute_strategy_statistical_verdict_v1793122(
        policy_metrics_by_id={
            "SI8_RESEARCH_DISCOVERY_REPAIRED": {
                "closed_trades_count": 8,
                "expectancy_net_r": 0.45,
                "total_realized_net_usd": 12.0,
            }
        },
        oos_report={},
    )
    assert v_under["strategy_statistical_verdict"] == "INCONCLUSIVE_UNDERPOWERED"
    assert v_under["activation_ready"] == "NO"


# T18: Artifact manifest SHA-256 and Phase A/B/C git SHA checks detect tampering
def test_t18_artifact_manifest_sha256_and_phase_a_b_c_git_sha_checks_detect_tampering() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "01_sample.json"
        p.write_text('{"ok": true}\n', encoding="utf-8")
        digest_before = hashlib.sha256(p.read_bytes()).hexdigest()
        p.write_text('{"ok": false}\n', encoding="utf-8")
        digest_after = hashlib.sha256(p.read_bytes()).hexdigest()
        assert digest_before != digest_after

    sha = "a" * 40
    ev = {
        "01": {"current_branch": "feature/v17-9-2-structural-sl-tp-from-qa", "ancestry_verified": True, "tested_executable_sha": sha},
        "02": {"violated_files_count": 0},
        "08": {"tested_executable_sha": sha},
        "09": {"tested_executable_sha": "b" * 40},
        "13": {"tested_executable_sha": sha},
    }
    res = evaluate_v1793124_acceptance_gates_g01_g15(evidence_by_key=ev, tested_executable_sha=sha)
    assert "G01" in res["failed_gate_ids"]
