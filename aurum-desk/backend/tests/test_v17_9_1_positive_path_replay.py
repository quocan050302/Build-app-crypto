"""
Aurum Desk V17.9.1.1 — Positive-Path Historical Validation, Zero-Eligible Root-Cause
Attribution, Multi-Worker Determinism & Strict Negative Controls Test Suite.
"""

from __future__ import annotations

import bisect
import concurrent.futures
import copy
import hashlib
from pathlib import Path
from typing import Any, Dict, List

import pytest

from domain_calculator import CostAssumptions, calculate_risk_reward
from services.market_shadow_service import MarketShadowService
from services.mtf_structure_liquidity_service import (
    MtfDataQualityState,
    _simulate_forward_trade_outcome,
    build_variant_market_context,
    compare_ab_variant_decisions,
    compare_actual_consumed_decisions,
    normalize_market_bars,
    validate_causal_window,
)
from services.scalp_m1_types import get_scalp_config
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator
from services.shared_strategy_evaluator import (
    DecisionStatus,
    PRODUCTION_MODEL_IDS,
    SharedStrategyEvaluator,
    build_market_decision_context,
    canonical_is_executable,
    validate_canonical_executability,
)
from services.v17_8_backtest_runner import (
    SAVED_DATASET_GZ_PATH,
    collect_or_load_verified_historical_dataset,
    evaluate_production_parity_model_snapshot,
)
from services.v17_9_0_1_baseline_oracle import IndependentPreRefactorBaselineOracle
from services.v17_9_1_decision_funnel import (
    build_decision_funnel_report,
    build_function_truth_map,
    build_per_decision_trace_record,
    classify_historical_dataset_gaps,
    classify_rejection_stage,
    evaluate_decision_counterfactuals_research_only,
    extract_decision_numerical_operands,
    scan_full_m1_candidate_universe,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def verified_datasets() -> Dict[str, Any]:
    return collect_or_load_verified_historical_dataset(symbol="XAUUSDT", allow_network_refresh=False)


def _eval_checkpoint_subset(indices: List[int]) -> List[Dict[str, Any]]:
    ds = collect_or_load_verified_historical_dataset(symbol="XAUUSDT", allow_network_refresh=False)
    bars_1m, bars_5m, bars_15m, bars_1h, bars_4h, bars_1d = (
        ds["1m"],
        ds["5m"],
        ds["15m"],
        ds["1H"],
        ds["4H"],
        ds["1D"],
    )
    c5 = [int(b["close_time"]) for b in bars_5m]
    c15 = [int(b["close_time"]) for b in bars_15m]
    c1h = [int(b["close_time"]) for b in bars_1h]
    c4h = [int(b["close_time"]) for b in bars_4h]
    c1d = [int(b["close_time"]) for b in bars_1d]

    out: List[Dict[str, Any]] = []
    for i_1m in indices:
        asof = int(bars_1m[i_1m]["close_time"])
        i5 = bisect.bisect_right(c5, asof)
        i15 = bisect.bisect_right(c15, asof)
        i1h = bisect.bisect_right(c1h, asof)
        i4h = bisect.bisect_right(c4h, asof)
        i1d = bisect.bisect_right(c1d, asof)
        w1 = bars_1m[max(0, i_1m + 1 - 120) : i_1m + 1]
        c = float(w1[-1]["close"])
        ctx = build_market_decision_context(
            symbol="XAUUSDT",
            decision_timestamp_ms=asof,
            m1_candles=w1,
            m5_candles=bars_5m[max(0, i5 - 96) : i5],
            m15_candles=bars_15m[max(0, i15 - 96) : i15],
            h1_candles=bars_1h[max(0, i1h - 96) : i1h],
            h4_candles=bars_4h[max(0, i4h - 64) : i4h],
            d1_candles=bars_1d[max(0, i1d - 30) : i1d],
            bid=round(c - 0.10, 2),
            ask=round(c + 0.10, 2),
            policy_config={
                "mtf_structure_observe_only": True,
                "mtf_structure_eligibility_enabled": False,
            },
        )
        pref = SharedStrategyEvaluator.resolve_canonical_preferred_direction(ctx)
        for m_id in PRODUCTION_MODEL_IDS:
            snap = SharedStrategyEvaluator.evaluate_model_direction(ctx, m_id, pref)
            out.append(
                {
                    "i_1m": i_1m,
                    "decision_id": snap.decision_id,
                    "model_id": m_id,
                    "direction": pref,
                    "decision_status": snap.decision_status,
                    "trade_action_fingerprint": snap.trade_action_fingerprint,
                    "policy_fingerprint": snap.policy_fingerprint,
                    "metadata_fingerprint": snap.metadata_fingerprint,
                }
            )
    return out


def test_01_function_truth_map_ast_completeness() -> None:
    """Section 02: Verify all mapped functions across Sections 02.A-J exist in the repository AST."""
    truth_map = build_function_truth_map(REPO_ROOT)
    assert truth_map["all_verified"] is True, f"Missing AST symbols: {[e for e in truth_map['entries'] if not e['exists_in_ast']]}"
    assert truth_map["missing_in_ast_count"] == 0
    assert truth_map["total_mapped_symbols"] >= 37
    for entry in truth_map["entries"]:
        assert entry["exists_in_ast"] is True
        assert entry["line_number"] is not None and entry["line_number"] > 0
        assert entry["signature"] not in ("MISSING", "FILE_MISSING")


def test_02_dataset_sha256_and_gap_classification_no_synthetic_fill(verified_datasets: Dict[str, Any]) -> None:
    """Section 04.B: Verify canonical dataset SHA-256 and gap classification without synthetic fill."""
    assert SAVED_DATASET_GZ_PATH.exists()
    raw_sha = hashlib.sha256(SAVED_DATASET_GZ_PATH.read_bytes()).hexdigest()
    assert raw_sha == "9f301fee2e620c882b205b857fc63b73c3e36f550161f4100e64fcba7ff98c18"

    cov = classify_historical_dataset_gaps(verified_datasets)
    assert cov["synthetic_fill_applied"] is False
    assert cov["interpolation_applied"] is False
    assert cov["forward_fill_applied"] is False
    assert cov["quote_construction_mode"] == "MODELED_SPREAD_QUOTE"

    m1_cov = cov["timeframes"]["M1"]
    assert m1_cov["actual_bars"] == 22376
    assert m1_cov["total_missing_bars"] == 6926
    assert m1_cov["out_of_order_count"] == 0
    assert m1_cov["duplicate_count"] == 0
    assert sum(m1_cov["missing_bars_by_category"].values()) == 6926
    assert m1_cov["missing_bars_by_category"].get("WEEKEND_MARKET_CLOSED", 0) > 0

    assert cov["timeframes"]["M5"]["actual_bars"] == 31609
    assert cov["timeframes"]["M5"]["total_missing_bars"] == 923
    assert cov["timeframes"]["M15"]["actual_bars"] == 15090
    assert cov["timeframes"]["M15"]["total_missing_bars"] == 362
    assert cov["timeframes"]["H1"]["actual_bars"] == 3863
    assert cov["timeframes"]["H4"]["actual_bars"] == 965
    assert cov["timeframes"]["D1"]["actual_bars"] == 181


def test_03_net_rr_algebraic_decomposition_and_no_gate_loosening(verified_datasets: Dict[str, Any]) -> None:
    """
    Section 03.D & Constraint #5: Verify risk gates are untouched (min_net_rr=2.0, taker_fee=0.0004,
    slippage=0.10) and verify exact algebraic decomposition of NET_RR_TOO_LOW.
    """
    scalp_cfg = get_scalp_config()
    assert float(scalp_cfg.min_net_rr) == 2.0

    cost_cfg = CostAssumptions()
    assert float(cost_cfg.taker_fee_rate) == 0.0004
    assert float(cost_cfg.maker_fee_rate) == 0.0002
    assert float(cost_cfg.slippage_usd) == 0.10
    assert cost_cfg.tp_is_maker is False

    # Inspect a real checkpoint from the 121-checkpoint grid where setup is VALID but rejected by NET_RR_TOO_LOW
    bars_1m = verified_datasets["1m"]
    bars_5m = verified_datasets["5m"]
    bars_15m = verified_datasets["15m"]
    bars_1h = verified_datasets["1H"]
    bars_4h = verified_datasets["4H"]
    bars_1d = verified_datasets["1D"]

    i_1m = 214  # First checkpoint after start_idx=30 with stride=184
    asof = int(bars_1m[i_1m]["close_time"])
    w1 = bars_1m[max(0, i_1m + 1 - 120) : i_1m + 1]
    c = float(w1[-1]["close"])
    ctx = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=asof,
        m1_candles=w1,
        m5_candles=[b for b in bars_5m if int(b["close_time"]) <= asof][-96:],
        m15_candles=[b for b in bars_15m if int(b["close_time"]) <= asof][-96:],
        h1_candles=[b for b in bars_1h if int(b["close_time"]) <= asof][-96:],
        h4_candles=[b for b in bars_4h if int(b["close_time"]) <= asof][-64:],
        d1_candles=[b for b in bars_1d if int(b["close_time"]) <= asof][-30:],
        bid=round(c - 0.10, 2),
        ask=round(c + 0.10, 2),
        policy_config={"mtf_structure_observe_only": False, "mtf_structure_eligibility_enabled": False},
    )
    pref = SharedStrategyEvaluator.resolve_canonical_preferred_direction(ctx)
    snaps = [SharedStrategyEvaluator.evaluate_model_direction(ctx, m_id, pref) for m_id in PRODUCTION_MODEL_IDS]
    snap = next(
        s
        for s in snaps
        if s.setup_validity == "VALID"
        and any(str(r).startswith("NET_RR_TOO_LOW") for r in s.rejection_reason_codes)
    )
    assert snap.setup_validity == "VALID"
    assert any(str(r).startswith("NET_RR_TOO_LOW") for r in snap.rejection_reason_codes)

    ops = extract_decision_numerical_operands(snap, ctx)
    assert ops["has_complete_price_geometry"] is True
    assert ops["min_net_rr_threshold"] == 2.0
    assert ops["target_distance_shortfall_usd"] > 0.0
    assert ops["required_target_distance_for_min_rr_usd"] > ops["target_distance"]

    # Cross-check against domain_calculator.calculate_risk_reward
    rr_calc = calculate_risk_reward(
        direction=str(ops["direction"]),
        planned_entry=float(ops["entry_reference"]),
        stop_loss=float(ops["selected_sl"]),
        take_profit=float(ops["take_profit"]),
        capital_usdt=float(ops["capital_usdt"]),
        risk_pct=float(ops["risk_pct"]),
        leverage=int(ops["leverage"]),
    )
    assert abs(float(rr_calc.net_rr) - float(ops["net_rr"])) <= 1e-3

    cf = evaluate_decision_counterfactuals_research_only(snap, ctx)
    assert cf["research_tag"] == "NON_EXECUTABLE_RESEARCH_ONLY"
    assert cf["applicable"] is True
    assert cf["canonical_passes_min_rr_2_0"] is False


def test_04_two_stage_candidate_scan_and_121_checkpoint_funnel_reconciliation(
    verified_datasets: Dict[str, Any],
) -> None:
    """
    Section 03.C & 04.A: Verify Stage-1 candidate scanner covers all 22,346 M1 checkpoints
    and verify exact Decision Funnel reconciliation on the 121-checkpoint V17.9.1 cohort.
    """
    scan = scan_full_m1_candidate_universe(verified_datasets, min_candidate_target_distance_usd=11.0)
    assert scan["total_m1_bars"] == 22376
    assert scan["valid_chronological_m1_universe"] == 22346
    assert scan["stage1_scanned_checkpoints"] == 22346
    assert scan["stage1_coverage_ratio"] == 1.0
    assert scan["stage1_pref_candidate_count"] == 133
    assert scan["stage1_candidate_count"] == 380

    # Verify the 121-checkpoint uniform grid from V17.9.1
    bars_1m = verified_datasets["1m"]
    bars_5m = verified_datasets["5m"]
    bars_15m = verified_datasets["15m"]
    bars_1h = verified_datasets["1H"]
    bars_4h = verified_datasets["4H"]
    bars_1d = verified_datasets["1D"]
    c5 = [int(b["close_time"]) for b in bars_5m]
    c15 = [int(b["close_time"]) for b in bars_15m]
    c1h = [int(b["close_time"]) for b in bars_1h]
    c4h = [int(b["close_time"]) for b in bars_4h]
    c1d = [int(b["close_time"]) for b in bars_1d]

    start_idx = 30
    end_idx = len(bars_1m) - 1
    stride = max(1, (end_idx - start_idx) // 120)
    eval_indices_121 = list(range(start_idx, end_idx + 1, stride))
    assert len(eval_indices_121) == 121

    records_121: List[Dict[str, Any]] = []
    for pos_idx, i_1m in enumerate(eval_indices_121):
        asof = int(bars_1m[i_1m]["close_time"])
        part = "TRAIN" if pos_idx < 60 else ("VALIDATION" if pos_idx < 90 else "OUT_OF_SAMPLE")
        i5 = bisect.bisect_right(c5, asof)
        i15 = bisect.bisect_right(c15, asof)
        i1h = bisect.bisect_right(c1h, asof)
        i4h = bisect.bisect_right(c4h, asof)
        i1d = bisect.bisect_right(c1d, asof)
        w1 = bars_1m[max(0, i_1m + 1 - 120) : i_1m + 1]
        c = float(w1[-1]["close"])
        ctx = build_market_decision_context(
            symbol="XAUUSDT",
            decision_timestamp_ms=asof,
            m1_candles=w1,
            m5_candles=bars_5m[max(0, i5 - 96) : i5],
            m15_candles=bars_15m[max(0, i15 - 96) : i15],
            h1_candles=bars_1h[max(0, i1h - 96) : i1h],
            h4_candles=bars_4h[max(0, i4h - 64) : i4h],
            d1_candles=bars_1d[max(0, i1d - 30) : i1d],
            bid=round(c - 0.10, 2),
            ask=round(c + 0.10, 2),
            policy_config={"mtf_structure_observe_only": False, "mtf_structure_eligibility_enabled": False},
        )
        pref = SharedStrategyEvaluator.resolve_canonical_preferred_direction(ctx)
        for m_id in PRODUCTION_MODEL_IDS:
            snap = SharedStrategyEvaluator.evaluate_model_direction(ctx, m_id, pref)
            records_121.append(
                {
                    "decision_id": snap.decision_id,
                    "decision_timestamp_ms": asof,
                    "model_id": m_id,
                    "direction": pref,
                    "partition": part,
                    "setup_validity": snap.setup_validity,
                    "decision_status": snap.decision_status,
                    "take_profit": snap.take_profit,
                    "selected_sl": snap.selected_sl,
                    "rejection_reason_codes": list(snap.rejection_reason_codes),
                }
            )

    funnel_121 = build_decision_funnel_report(records_121, cohort_label="V17_9_1_SPARSE_121_COHORT")
    assert funnel_121["unique_decision_count"] == 605
    assert funnel_121["valid_setup_count"] == 349
    assert funnel_121["canonical_executable_eligible_count"] == 0
    assert funnel_121["reconciliation"]["unique_decisions_equals_first_blocking_plus_eligible"] is True
    assert funnel_121["reconciliation"]["sum_first_blocking_rejected"] == 605
    rej = funnel_121["rejection_reason_breakdown"]
    assert rej["NET_RR_TOO_LOW"]["occurrence_count"] == 302
    assert rej["WAITING_SWEEP_RECLAIM"]["occurrence_count"] == 138
    assert rej["WAITING_M1_BOS"]["occurrence_count"] == 94
    assert rej["SHADOW_REJECTED_NO_VALID_TARGET"]["occurrence_count"] == 42
    assert rej["NO_VALID_FVG_SETUP"]["occurrence_count"] == 24
    assert rej["STRUCTURAL_ANCHOR_NOT_FOUND"]["occurrence_count"] == 6
    assert rej["ATR_MISSING"]["occurrence_count"] == 5
    assert rej["NON_CONTIGUOUS_BARS"]["occurrence_count"] == 5
    assert rej["ANCHOR_ALREADY_INVALIDATED"]["occurrence_count"] == 1


def test_05_real_historical_positive_path_eligible_decisions_and_3way_oracle_parity(
    verified_datasets: Dict[str, Any],
) -> None:
    """
    Section 05 & P08: Verify real historical canonical ELIGIBLE decisions on XAUUSDT,
    prove 3-runtime adapter parity + IndependentPreRefactorBaselineOracle parity + A vs B invariance,
    and verify causal forward trade outcome resolution.
    """
    bars_1m = verified_datasets["1m"]
    bars_5m = verified_datasets["5m"]
    bars_15m = verified_datasets["15m"]
    bars_1h = verified_datasets["1H"]
    bars_4h = verified_datasets["4H"]
    bars_1d = verified_datasets["1D"]
    c5 = [int(b["close_time"]) for b in bars_5m]
    c15 = [int(b["close_time"]) for b in bars_15m]
    c1h = [int(b["close_time"]) for b in bars_1h]
    c4h = [int(b["close_time"]) for b in bars_4h]
    c1d = [int(b["close_time"]) for b in bars_1d]

    prod_orch = ScalpWatcherOrchestrator()
    shadow_svc = MarketShadowService(symbol="XAUUSDT")

    # Known real historical M1 indices with canonical ELIGIBLE setups from full scan
    positive_indices = [6652, 6676, 6886, 6891, 14473]
    eligible_count = 0
    resolved_count = 0

    for i_1m in positive_indices:
        asof = int(bars_1m[i_1m]["close_time"])
        i5 = bisect.bisect_right(c5, asof)
        i15 = bisect.bisect_right(c15, asof)
        i1h = bisect.bisect_right(c1h, asof)
        i4h = bisect.bisect_right(c4h, asof)
        i1d = bisect.bisect_right(c1d, asof)
        w1 = bars_1m[max(0, i_1m + 1 - 120) : i_1m + 1]
        fwd_1m = bars_1m[i_1m + 1 : min(len(bars_1m), i_1m + 1 + 180)]
        c = float(w1[-1]["close"])

        base_inputs = {
            "symbol": "XAUUSDT",
            "decision_timestamp_ms": asof,
            "m1_candles": w1,
            "m5_candles=bars_5m": None,
            "m5_candles": bars_5m[max(0, i5 - 96) : i5],
            "m15_candles": bars_15m[max(0, i15 - 96) : i15],
            "h1_candles": bars_1h[max(0, i1h - 96) : i1h],
            "h4_candles": bars_4h[max(0, i4h - 64) : i4h],
            "d1_candles": bars_1d[max(0, i1d - 30) : i1d],
            "bid": round(c - 0.10, 2),
            "ask": round(c + 0.10, 2),
        }
        base_inputs.pop("m5_candles=bars_5m", None)

        ctx_a = build_variant_market_context(
            base_inputs,
            {"mtf_structure_observe_only": False, "mtf_structure_eligibility_enabled": False, "mtf_ablation_mode": "FULL"},
            decision_time=asof,
        )
        ctx_b = build_variant_market_context(
            base_inputs,
            {"mtf_structure_observe_only": True, "mtf_structure_eligibility_enabled": False, "mtf_ablation_mode": "FULL"},
            decision_time=asof,
        )
        pref_dir = SharedStrategyEvaluator.resolve_canonical_preferred_direction(ctx_a)

        for m_id in PRODUCTION_MODEL_IDS:
            snap_a = prod_orch.evaluate_shared_decision_snapshot(context=ctx_a, model_id=m_id, direction=pref_dir)
            oracle_dict = IndependentPreRefactorBaselineOracle.evaluate_baseline_dict(
                context=ctx_a, model_id=m_id, direction=pref_dir
            )
            snap_b_prod = prod_orch.evaluate_shared_decision_snapshot(context=ctx_b, model_id=m_id, direction=pref_dir)
            snap_b_shadow = shadow_svc.evaluate_shared_decision_snapshot(context=ctx_b, model_id=m_id, direction=pref_dir)
            snap_b_bt = evaluate_production_parity_model_snapshot(
                model_id=m_id, context=ctx_b, direction=pref_dir
            )["shared_decision_snapshot"]

            ab_cmp = compare_ab_variant_decisions(ctx_a, snap_a, ctx_b, snap_b_prod)
            assert ab_cmp["is_valid"] is True, f"A vs B failed at {i_1m} {m_id}: {ab_cmp['mismatches']}"

            p_3way = compare_actual_consumed_decisions(
                snap_b_prod, snap_b_shadow, snap_b_bt, independent_oracle=oracle_dict
            )
            assert p_3way["is_parity"] is True, f"3-way parity failed at {i_1m} {m_id}: {p_3way['mismatches']}"

            if snap_b_prod.decision_status == DecisionStatus.ELIGIBLE.value:
                eligible_count += 1
                assert canonical_is_executable(snap_b_prod) is True
                exec_ok, exec_rej = validate_canonical_executability(snap_b_prod, context=ctx_b)
                assert exec_ok is True and exec_rej is None
                assert snap_b_prod.net_rr is not None and float(snap_b_prod.net_rr) >= 2.0
                assert snap_b_prod.hypothetical_quantity is not None and float(snap_b_prod.hypothetical_quantity) > 0

                sim = _simulate_forward_trade_outcome(
                    snap_b_prod.direction,
                    float(snap_b_prod.entry_reference),
                    float(snap_b_prod.selected_sl),
                    float(snap_b_prod.take_profit),
                    fwd_1m,
                )
                if sim["outcome"] in ("WIN", "LOSS", "LOSS_TIE_CONSERVATIVE"):
                    resolved_count += 1

                trace = build_per_decision_trace_record(
                    run_id="test_run",
                    code_sha="0" * 40,
                    executable_tree_hash="1" * 64,
                    dataset_sha256="9f301fee2e620c882b205b857fc63b73c3e36f550161f4100e64fcba7ff98c18",
                    config_hash="cfg_hash",
                    source_timeframe_hashes={"M1": "m1_hash"},
                    snapshot=snap_b_prod,
                    context=ctx_b,
                    outcome_sim=sim,
                    forward_m1_bars=fwd_1m,
                    checkpoint_index_1m=i_1m,
                )
                assert trace["decision_status"] == "ELIGIBLE"
                assert trace["first_blocking_stage"] == "STAGE_07_CANONICAL_EXECUTABLE_ELIGIBLE"
                assert trace["execution_sim_status"] == "SIMULATED_ELIGIBLE_MARKET_ENTRY_ON_CLOSE"
                assert trace["outcome_label"] in ("WIN", "LOSS", "LOSS_TIE_CONSERVATIVE", "OPEN_AT_HORIZON")

    assert eligible_count >= 10
    assert resolved_count >= 5


def test_06_prefix_invariance_and_future_candle_mutation_isolation(verified_datasets: Dict[str, Any]) -> None:
    """
    Section 04.H: Prefix invariance & future candle mutation isolation on a real positive-path
    checkpoint (i_1m=6891). Appending or mutating future bars after decision_timestamp_ms must
    have zero effect on MarketDecisionContext, MtfEvaluationContext, and StrategyDecisionSnapshot.
    """
    bars_1m = verified_datasets["1m"]
    bars_5m = verified_datasets["5m"]
    bars_15m = verified_datasets["15m"]
    bars_1h = verified_datasets["1H"]
    bars_4h = verified_datasets["4H"]
    bars_1d = verified_datasets["1D"]

    i_1m = 6891
    asof = int(bars_1m[i_1m]["close_time"])
    w1_clean = bars_1m[max(0, i_1m + 1 - 120) : i_1m + 1]
    w5_clean = [b for b in bars_5m if int(b["close_time"]) <= asof][-96:]
    w15_clean = [b for b in bars_15m if int(b["close_time"]) <= asof][-96:]
    w1h_clean = [b for b in bars_1h if int(b["close_time"]) <= asof][-96:]
    w4h_clean = [b for b in bars_4h if int(b["close_time"]) <= asof][-64:]
    w1d_clean = [b for b in bars_1d if int(b["close_time"]) <= asof][-30:]
    c = float(w1_clean[-1]["close"])

    ctx_clean = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=asof,
        m1_candles=w1_clean,
        m5_candles=w5_clean,
        m15_candles=w15_clean,
        h1_candles=w1h_clean,
        h4_candles=w4h_clean,
        d1_candles=w1d_clean,
        bid=round(c - 0.10, 2),
        ask=round(c + 0.10, 2),
        strict_causal_validation=False,
        policy_config={"mtf_structure_observe_only": True, "mtf_structure_eligibility_enabled": False},
    )
    snap_clean = SharedStrategyEvaluator.evaluate_model_direction(ctx_clean, "SMC_M1_FVG_CONTINUATION", "SHORT")
    assert snap_clean.decision_status == DecisionStatus.ELIGIBLE.value

    # Append mutated future candles after `asof` with extreme fake prices ($9999 / $1000)
    future_m1 = copy.deepcopy(bars_1m[i_1m + 1 : i_1m + 15])
    for fb in future_m1:
        fb["high"] = 9999.0
        fb["low"] = 1000.0
        fb["close"] = 9999.0

    ctx_with_future = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=asof,
        m1_candles=list(w1_clean) + future_m1,
        m5_candles=w5_clean,
        m15_candles=w15_clean,
        h1_candles=w1h_clean,
        h4_candles=w4h_clean,
        d1_candles=w1d_clean,
        bid=round(c - 0.10, 2),
        ask=round(c + 0.10, 2),
        strict_causal_validation=False,
        policy_config={"mtf_structure_observe_only": True, "mtf_structure_eligibility_enabled": False},
    )
    snap_with_future = SharedStrategyEvaluator.evaluate_model_direction(
        ctx_with_future, "SMC_M1_FVG_CONTINUATION", "SHORT"
    )

    assert snap_clean.decision_fingerprint == snap_with_future.decision_fingerprint
    assert snap_clean.trade_action_fingerprint == snap_with_future.trade_action_fingerprint
    assert snap_clean.mtf_context_hash == snap_with_future.mtf_context_hash
    assert snap_clean.entry_reference == snap_with_future.entry_reference
    assert snap_clean.selected_sl == snap_with_future.selected_sl
    assert snap_clean.take_profit == snap_with_future.take_profit


def test_07_negative_controls_tampered_parity_and_execution_guards(verified_datasets: Dict[str, Any]) -> None:
    """
    Section 04.H & 07.A(5): Negative controls for tampered parity, invalid eligible fields,
    same-bar SL/TP tie, open-at-horizon accounting, non-monotonic bars, and invalid direction.
    """
    # 1. Same-bar SL and TP tie in _simulate_forward_trade_outcome must resolve conservatively to LOSS_TIE_CONSERVATIVE
    tie_bar = [{"open": 4100.0, "high": 4125.0, "low": 4090.0, "close": 4110.0, "close_time": 1792000060000}]
    sim_tie_long = _simulate_forward_trade_outcome("LONG", 4100.0, 4095.0, 4120.0, tie_bar)
    assert sim_tie_long["outcome"] == "LOSS_TIE_CONSERVATIVE"
    assert sim_tie_long["realized_r"] == -1.0

    sim_tie_short = _simulate_forward_trade_outcome("SHORT", 4100.0, 4105.0, 4092.0, tie_bar)
    assert sim_tie_short["outcome"] == "LOSS_TIE_CONSERVATIVE"
    assert sim_tie_short["realized_r"] == -1.0

    # 2. Open-at-horizon trade must be labeled OPEN_AT_HORIZON, not WIN/LOSS
    flat_bar = [{"open": 4100.0, "high": 4101.0, "low": 4099.0, "close": 4100.5, "close_time": 1792000060000}]
    sim_open = _simulate_forward_trade_outcome("LONG", 4100.0, 4090.0, 4130.0, flat_bar)
    assert sim_open["outcome"] == "OPEN_AT_HORIZON"

    # 3. Non-monotonic timestamps in normalize_market_bars
    bad_bars = [
        {"open_time": 1792000120000, "close_time": 1792000180000, "open": 4100, "high": 4101, "low": 4099, "close": 4100},
        {"open_time": 1792000060000, "close_time": 1792000120000, "open": 4100, "high": 4101, "low": 4099, "close": 4100},
    ]
    norm_bad = normalize_market_bars("XAUUSDT", "M1", bad_bars)
    assert norm_bad.out_of_order_count >= 1

    # 4. Tampered ELIGIBLE decision with null SL or invalid geometry must fail compare_actual_consumed_decisions
    bars_1m = verified_datasets["1m"]
    i_1m = 6891
    asof = int(bars_1m[i_1m]["close_time"])
    w1 = bars_1m[max(0, i_1m + 1 - 120) : i_1m + 1]
    c = float(w1[-1]["close"])
    ctx = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=asof,
        m1_candles=w1,
        m5_candles=[b for b in verified_datasets["5m"] if int(b["close_time"]) <= asof][-96:],
        m15_candles=[b for b in verified_datasets["15m"] if int(b["close_time"]) <= asof][-96:],
        h1_candles=[b for b in verified_datasets["1H"] if int(b["close_time"]) <= asof][-96:],
        h4_candles=[b for b in verified_datasets["4H"] if int(b["close_time"]) <= asof][-64:],
        d1_candles=[b for b in verified_datasets["1D"] if int(b["close_time"]) <= asof][-30:],
        bid=round(c - 0.10, 2),
        ask=round(c + 0.10, 2),
        policy_config={"mtf_structure_observe_only": True, "mtf_structure_eligibility_enabled": False},
    )
    prod_orch = ScalpWatcherOrchestrator()
    shadow_svc = MarketShadowService(symbol="XAUUSDT")
    s_prod = prod_orch.evaluate_shared_decision_snapshot(context=ctx, model_id="SMC_M1_FVG_CONTINUATION", direction="SHORT")
    s_shad = shadow_svc.evaluate_shared_decision_snapshot(context=ctx, model_id="SMC_M1_FVG_CONTINUATION", direction="SHORT")
    s_bt = evaluate_production_parity_model_snapshot(
        model_id="SMC_M1_FVG_CONTINUATION", context=ctx, direction="SHORT"
    )["shared_decision_snapshot"]

    assert s_prod.decision_status == "ELIGIBLE"
    # Tamper selected_sl to None on all 3 dicts -> must fail closed!
    d_p = s_prod.to_parity_dict()
    d_s = s_shad.to_parity_dict()
    d_b = dict(s_bt) if isinstance(s_bt, dict) else s_bt.to_parity_dict()
    d_p["selected_sl"] = None
    d_s["selected_sl"] = None
    d_b["selected_sl"] = None
    res_null_sl = compare_actual_consumed_decisions(d_p, d_s, d_b)
    assert res_null_sl["is_parity"] is False
    assert res_null_sl["reason"] == "MISSING_MANDATORY_PARITY_FIELD"

    # Tamper SHORT geometry so SL < entry (invalid for SHORT) on all 3 dicts -> must fail closed!
    d_p2 = s_prod.to_parity_dict()
    d_s2 = s_shad.to_parity_dict()
    d_b2 = dict(s_bt) if isinstance(s_bt, dict) else s_bt.to_parity_dict()
    d_p2["selected_sl"] = float(d_p2["entry_reference"]) - 5.0
    d_s2["selected_sl"] = float(d_s2["entry_reference"]) - 5.0
    d_b2["selected_sl"] = float(d_b2["entry_reference"]) - 5.0
    res_bad_geom = compare_actual_consumed_decisions(d_p2, d_s2, d_b2)
    assert res_bad_geom["is_parity"] is False


def test_08_multi_worker_vs_single_worker_deterministic_equivalence() -> None:
    """
    Section 07.B: Verify that evaluating chronological checkpoints across 1 worker vs
    multiple process workers produces bit-for-bit identical ordered decisions and fingerprints.
    """
    test_indices = [214, 6132, 6652, 6676, 6886, 6887, 6891, 14473]
    serial_results = _eval_checkpoint_subset(test_indices)

    chunks = [test_indices[:4], test_indices[4:]]
    with concurrent.futures.ProcessPoolExecutor(max_workers=2) as pool:
        chunk_outputs = list(pool.map(_eval_checkpoint_subset, chunks))
    parallel_results = [item for sublist in chunk_outputs for item in sublist]

    assert len(serial_results) == len(parallel_results) == len(test_indices) * 5
    assert serial_results == parallel_results

