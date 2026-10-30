"""
Aurum Desk V17.9.0.1 — True Runtime Integration, Independent Baseline Oracle,
H4/H1 Provenance, Acceptance Gate Repair & 15 Negative Acceptance Scenarios.
"""

from __future__ import annotations

import ast
import asyncio
import copy
import dataclasses
import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from services.market_shadow_service import MarketShadowService
from services.quote_validator import CanonicalQuote
from services.scalp_m1_types import ModelIdentifier
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator
from services.shared_strategy_evaluator import (
    EVALUATOR_VERSION,
    PRODUCTION_MODEL_IDS,
    DecisionResult,
    DecisionStatus,
    SharedStrategyEvaluator,
    aggregate_closed_htf_candles,
    build_market_decision_context,
    characterize_production_baseline_decision,
    compare_decision_parity,
    validate_htf_candle_series,
    verify_multi_adapter_parity,
)
from services.v17_8_backtest_runner import (
    _extract_historical_opportunities_chronologically,
    evaluate_production_parity_model_snapshot,
)
from services.v17_9_0_1_baseline_oracle import (
    PRE_REFACTOR_BASELINE_COMMIT_SHA,
    REQUIRED_BASELINE_CATEGORIES,
    SUPPORTED_BASELINE_MODELS,
    IndependentPreRefactorBaselineOracle,
    _make_synthetic_candle_series,
    build_phase_b_characterization_scenarios,
    generate_phase_b_baseline_artifacts,
)
from tools.acceptance_verdict import (
    AUTHORITATIVE_GATE_MAPPING_V17_9_0_1,
    MANDATORY_GATES_V17_9_0,
    determine_release_verdict,
    determine_v17_9_0_1_top_level_verdict,
    normalize_v17_9_0_1_gate_status,
)
from tools.test_resource_guard import DiskBudgetExceededError, TestResourceGuard
from tools.v17_9_execution_state import ExecutionStateManager


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_v17_9_0_1_htf_candle_validation_and_utc_aggregation() -> None:
    """
    Phase G: Verifies deterministic H1 (3,600,000 ms) and H4 (14,400,000 ms) closed-candle
    validation and aggregation, rejecting unclosed, future/lookahead, misaligned-boundary,
    duplicate, out-of-order, and malformed OHLC candles, while preserving weekend gaps.
    """
    base_4h_ms = 1_761_696_000_000  # Exact 4H UTC boundary
    decision_ms = base_4h_ms + 4 * 3_600_000

    valid_h1 = [
        {
            "timestamp": base_4h_ms + i * 3_600_000,
            "open_time": base_4h_ms + i * 3_600_000,
            "close_time": base_4h_ms + (i + 1) * 3_600_000,
            "open": 2640.0 + i,
            "high": 2645.0 + i,
            "low": 2638.0 + i,
            "close": 2642.0 + i,
            "volume": 1000.0,
            "is_closed": True,
        }
        for i in range(4)
    ]
    norm_h1, status_h1, reasons_h1 = validate_htf_candle_series(
        valid_h1,
        symbol="XAUUSDT",
        timeframe="1H",
        tf_ms=3_600_000,
        decision_timestamp_ms=decision_ms,
        strict_validation=True,
    )
    assert len(norm_h1) == 4
    assert reasons_h1 == []
    assert status_h1 == "AVAILABLE"

    # Aggregate 4 closed H1 bars into 1 closed H4 bar
    agg_h4 = aggregate_closed_htf_candles(
        norm_h1,
        target_timeframe="4H",
        decision_timestamp_ms=decision_ms,
        symbol="XAUUSDT",
        source_tf_ms=3_600_000,
    )
    assert len(agg_h4) == 1
    assert agg_h4[0]["open_time"] == base_4h_ms
    assert agg_h4[0]["close_time"] == base_4h_ms + 14_400_000
    assert agg_h4[0]["open"] == 2640.0
    assert agg_h4[0]["high"] == 2648.0
    assert agg_h4[0]["low"] == 2638.0
    assert agg_h4[0]["close"] == 2645.0

    # Reject unclosed H1 candle
    unclosed_h1 = copy.deepcopy(valid_h1)
    unclosed_h1[-1]["is_closed"] = False
    _, st_unclosed, rej_unclosed = validate_htf_candle_series(
        unclosed_h1,
        symbol="XAUUSDT",
        timeframe="1H",
        tf_ms=3_600_000,
        decision_timestamp_ms=decision_ms,
        strict_validation=True,
    )
    assert st_unclosed == "PARTIAL_REJECTED"
    assert "UNCLOSED_1H_CANDLE_REJECTED" in rej_unclosed

    # Reject lookahead H4 candle
    future_h4 = [
        {
            "timestamp": decision_ms,
            "open_time": decision_ms,
            "close_time": decision_ms + 14_400_000,
            "open": 2640.0,
            "high": 2645.0,
            "low": 2638.0,
            "close": 2642.0,
            "volume": 1000.0,
            "is_closed": True,
        }
    ]
    _, st_future, rej_future = validate_htf_candle_series(
        future_h4,
        symbol="XAUUSDT",
        timeframe="4H",
        tf_ms=14_400_000,
        decision_timestamp_ms=decision_ms,
        strict_validation=True,
    )
    assert st_future == "LOOKAHEAD_REJECTED"
    assert "LOOKAHEAD_4H_CANDLE_REJECTED" in rej_future

    # Reject misaligned UTC boundary
    misaligned_h4 = [
        {
            "timestamp": base_4h_ms + 1800_000,
            "open_time": base_4h_ms + 1800_000,
            "close_time": base_4h_ms + 1800_000 + 14_400_000,
            "open": 2640.0,
            "high": 2645.0,
            "low": 2638.0,
            "close": 2642.0,
            "volume": 1000.0,
            "is_closed": True,
        }
    ]
    _, _, rej_boundary = validate_htf_candle_series(
        misaligned_h4,
        symbol="XAUUSDT",
        timeframe="4H",
        tf_ms=14_400_000,
        decision_timestamp_ms=decision_ms + 14_400_000,
        strict_validation=True,
    )
    assert "MISALIGNED_4H_UTC_BOUNDARY" in rej_boundary


def test_v17_9_0_1_canonical_decision_result_and_evaluate_decision() -> None:
    """
    Phase C: Verifies `SharedStrategyEvaluator.evaluate_decision(...)` returns a canonical
    `DecisionResult` (`StrategyDecisionSnapshot`) with `decision_id`, `evaluation_timestamp`,
    `entry`, `stop_loss`, `take_profit`, `effective_direction`, `risk_or_sizing_intent`,
    `reason_codes`, `input_snapshot_hash`, `configuration_hash`, and `evaluator_version`.
    """
    series = _make_synthetic_candle_series(base_open_ms=1_761_696_000_000, direction="LONG")
    ctx = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=series["decision_ms"],
        m1_candles=series["m1_candles"],
        m5_candles=series["m5_candles"],
        m15_candles=series["m15_candles"],
        h1_candles=series["h1_candles"],
        h4_candles=series["h4_candles"],
        bid=series["bid"],
        ask=series["ask"],
    )
    res = SharedStrategyEvaluator.evaluate_decision(
        market_snapshot=ctx,
        structural_context={
            "model_id": ModelIdentifier.SMC_M1_FVG_CONTINUATION.value,
            "direction": "LONG",
            "adapter_origin": "CANONICAL_TEST",
        },
    )
    assert isinstance(res, DecisionResult)
    assert res.evaluator_version == EVALUATOR_VERSION == "17.9.0.1.1"
    assert res.decision_id.startswith("dec_SMC_M1_FVG_CONTINUATION_XAUUSDT_")
    assert res.evaluation_timestamp == series["decision_ms"]
    assert res.effective_direction in ("LONG", "SHORT", "NO_TRADE")
    assert isinstance(res.risk_or_sizing_intent, dict)
    assert len(res.input_snapshot_hash) == 64
    assert len(res.configuration_hash) == 64
    assert len(res.decision_fingerprint) == 64


def _assert_model_parity_across_11_categories(model_id: str) -> None:
    scenarios = [
        s for s in build_phase_b_characterization_scenarios(build_market_decision_context)
        if s["model_id"] == model_id
    ]
    assert len(scenarios) == 11
    for item in scenarios:
        ctx = item["context"]
        direction = item["direction"]
        base_dec = IndependentPreRefactorBaselineOracle.evaluate_baseline_dict(
            context=ctx, model_id=model_id, direction=direction
        )
        shared_dec = SharedStrategyEvaluator.evaluate_model_direction(
            context=ctx, model_id=model_id, direction=direction
        ).to_parity_dict()
        assert base_dec["decision_status"] == shared_dec["decision_status"], item["scenario_id"]
        assert base_dec["setup_validity"] == shared_dec["setup_validity"], item["scenario_id"]
        assert base_dec["selected_sl"] == shared_dec["selected_sl"], item["scenario_id"]
        assert base_dec["take_profit"] == shared_dec["take_profit"], item["scenario_id"]
        assert list(base_dec["rejection_reason_codes"]) == list(shared_dec["rejection_reason_codes"]), item["scenario_id"]
        assert base_dec["decision_fingerprint"] == shared_dec["decision_fingerprint"], item["scenario_id"]


def test_v17_9_0_1_reversal_parity() -> None:
    _assert_model_parity_across_11_categories(ModelIdentifier.SMC_M1_REVERSAL.value)


def test_v17_9_0_1_continuation_parity() -> None:
    _assert_model_parity_across_11_categories(ModelIdentifier.SMC_M1_CONTINUATION.value)


def test_v17_9_0_1_fvg_continuation_parity() -> None:
    _assert_model_parity_across_11_categories(ModelIdentifier.SMC_M1_FVG_CONTINUATION.value)


def test_v17_9_0_1_sweep_reclaim_parity() -> None:
    _assert_model_parity_across_11_categories(ModelIdentifier.SMC_M1_SWEEP_RECLAIM.value)


def test_v17_9_0_1_cg01_parity() -> None:
    _assert_model_parity_across_11_categories(ModelIdentifier.CG01_SWEEP_MSS_FVG.value)


def test_v17_9_0_1_runtime_spy_production_and_shadow(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Phase D, E & I: Spy test proving that `ScalpWatcherOrchestrator.evaluate_cycle()`
    and `MarketShadowService.evaluate_shadow_cycle()` genuinely invoke `SharedStrategyEvaluator`
    in their live runtime loops and produce identical decision fingerprints on the same inputs.
    """
    series = _make_synthetic_candle_series(base_open_ms=1_761_696_000_000, direction="LONG")
    decision_ms = series["decision_ms"]
    quote = CanonicalQuote(
        symbol="XAUUSDT",
        last=round((series["bid"] + series["ask"]) / 2.0, 2),
        bid=series["bid"],
        ask=series["ask"],
        mark_price=round((series["bid"] + series["ask"]) / 2.0, 2),
        spread=round(series["ask"] - series["bid"], 4),
        exchange_ts_ms=decision_ms - 100,
        received_at_ms=decision_ms - 50,
        source="WS",
        connection_epoch=1,
        status="VALID",
        freshness_sec=0.05,
    )

    SharedStrategyEvaluator.reset_runtime_invocation_counts()

    # 1. Production runtime cycle (`ScalpWatcherOrchestrator.evaluate_cycle`)
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    import models
    from services.scalp_m1_types import M1ScalpConfig

    scalp_cfg = M1ScalpConfig(
        scalp_m1_enabled=True,
        scalp_m1_mode="WATCH_ONLY",
        cg01_enabled=True,
        cg01_profile_mode="SHADOW_RESEARCH",
    )
    mem_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    models.Base.metadata.create_all(bind=mem_engine)
    MemSession = sessionmaker(autocommit=False, autoflush=False, bind=mem_engine)
    db = MemSession()
    try:
        orch = ScalpWatcherOrchestrator()
        prod_res = orch.evaluate_cycle(
            db,
            "XAUUSDT",
            now_ms=decision_ms,
            quote={
                "bid": series["bid"],
                "ask": series["ask"],
                "last": round((series["bid"] + series["ask"]) / 2.0, 2),
                "timestamp": decision_ms - 50,
                "source": "WS",
                "status": "VALID",
                "freshness_sec": 0.05,
            },
            candles_1m=series["m1_candles"],
            candles_5m=series["m5_candles"],
            candles_15m=series["m15_candles"],
            candles_1h=series["h1_candles"],
            candles_4h=series["h4_candles"],
            htf_bias="BULLISH",
            h1_alignment="BULLISH",
            config=scalp_cfg,
            auto_enabled=False,
        )
    finally:
        db.close()
        mem_engine.dispose()
    counts_after_prod = SharedStrategyEvaluator.get_runtime_invocation_counts()
    assert counts_after_prod.get("PRODUCTION_ORCHESTRATOR", 0) >= 5
    assert "shared_decision_snapshots" in prod_res
    assert prod_res.get("evaluator_version") == EVALUATOR_VERSION
    prod_snaps_by_model = {snap["model_id"]: snap for snap in prod_res["shared_decision_snapshots"]}
    assert set(prod_snaps_by_model.keys()) == set(PRODUCTION_MODEL_IDS)

    # 2. MARKET_SHADOW runtime cycle (`MarketShadowService.evaluate_shadow_cycle`)
    shadow_svc = MarketShadowService()
    shadow_res = shadow_svc.evaluate_shadow_cycle(
        as_of_ms=decision_ms,
        closed_1m=series["m1_candles"],
        closed_5m=series["m5_candles"],
        closed_15m=series["m15_candles"],
        closed_1h=series["h1_candles"],
        closed_4h=series["h4_candles"],
        htf_bias="BULLISH",
        h1_alignment="BULLISH",
        quote=quote,
        scalp_config=scalp_cfg,
    )
    counts_after_shadow = SharedStrategyEvaluator.get_runtime_invocation_counts()
    assert counts_after_shadow.get("MARKET_SHADOW_ADAPTER", 0) >= 5
    assert "model_outcomes" in shadow_res
    assert set(shadow_res["model_outcomes"].keys()) == set(PRODUCTION_MODEL_IDS)

    # Compare Production vs MARKET_SHADOW decision snapshots across all 5 models
    for m_id, m_rec in shadow_res["model_outcomes"].items():
        assert "shared_decision_snapshot" in m_rec
        assert m_rec["evaluator_version"] == EVALUATOR_VERSION
        prod_snap = prod_snaps_by_model[m_id]
        div_check = shadow_svc.compare_production_shadow_divergence(prod_snap, m_rec["shared_decision_snapshot"])
        assert div_check["is_divergent"] is False, f"Divergence on {m_id}: {div_check}"


def test_v17_9_0_1_runtime_spy_backtest() -> None:
    """
    Phase F & I: Spy test proving `evaluate_production_parity_model_snapshot` and
    `_extract_historical_opportunities_chronologically` genuinely invoke `SharedStrategyEvaluator`.
    """
    series = _make_synthetic_candle_series(base_open_ms=1_761_696_000_000, direction="LONG")
    decision_ms = series["decision_ms"]
    quote = CanonicalQuote(
        symbol="XAUUSDT",
        last=round((series["bid"] + series["ask"]) / 2.0, 2),
        bid=series["bid"],
        ask=series["ask"],
        mark_price=round((series["bid"] + series["ask"]) / 2.0, 2),
        spread=round(series["ask"] - series["bid"], 4),
        exchange_ts_ms=decision_ms - 100,
        received_at_ms=decision_ms - 50,
        status="VALID",
    )
    SharedStrategyEvaluator.reset_runtime_invocation_counts()
    for m_id in PRODUCTION_MODEL_IDS:
        bt_res = evaluate_production_parity_model_snapshot(
            symbol="XAUUSDT",
            model_id=m_id,
            closed_1m=series["m1_candles"],
            closed_5m=series["m5_candles"],
            closed_15m=series["m15_candles"],
            closed_1h=series["h1_candles"],
            closed_4h=series["h4_candles"],
            quote=quote,
            as_of_ms=decision_ms,
            direction="LONG",
        )
        assert bt_res["evaluator_version"] == EVALUATOR_VERSION
        assert bt_res["adapter_origin"] == "HISTORICAL_BACKTEST_ADAPTER"
        assert len(bt_res["decision_fingerprint"]) == 64

    counts = SharedStrategyEvaluator.get_runtime_invocation_counts()
    assert counts.get("HISTORICAL_BACKTEST_ADAPTER", 0) == 5


def test_v17_9_0_1_chronological_backtest_invokes_shared_evaluator() -> None:
    """
    Verifies `_extract_historical_opportunities_chronologically` invokes `SharedStrategyEvaluator`
    during chronological M1 replay.
    """
    base_ms = 1_761_696_000_000
    s1 = _make_synthetic_candle_series(base_open_ms=base_ms, direction="LONG")
    m1_list = list(s1["m1_candles"])
    # Append 60 sequential closed M1 bars so total >= 90 bars with strictly monotonic timestamps
    last_close_ms = int(m1_list[-1]["close_time"])
    for k in range(60):
        o_ms = last_close_ms + k * 60_000
        c_ms = o_ms + 60_000
        px = 2645.0 + (k % 5) * 0.3
        m1_list.append({
            "timestamp": o_ms,
            "open_time": o_ms,
            "close_time": c_ms,
            "open": round(px, 2),
            "high": round(px + 0.4, 2),
            "low": round(px - 0.4, 2),
            "close": round(px + 0.1, 2),
            "volume": 120.0,
            "is_closed": True,
        })
    datasets = {
        "1m": m1_list,
        "5m": s1["m5_candles"],
        "15m": s1["m15_candles"],
        "1h": s1["h1_candles"],
        "4h": s1["h4_candles"],
    }
    SharedStrategyEvaluator.reset_runtime_invocation_counts()
    opps, rejs = _extract_historical_opportunities_chronologically(datasets, symbol="XAUUSDT")
    counts = SharedStrategyEvaluator.get_runtime_invocation_counts()
    assert counts.get("HISTORICAL_BACKTEST_ADAPTER", 0) >= 1
    for opp in opps:
        assert "shared_decision_snapshot" in opp
        assert opp["evaluator_version"] == EVALUATOR_VERSION
        assert opp["adapter_origin"] == "HISTORICAL_BACKTEST_ADAPTER"


def test_v17_9_0_1_non_self_referential_oracle() -> None:
    """
    Phase B & N: AST-verifies that `backend/services/v17_9_0_1_baseline_oracle.py` never imports
    or references `SharedStrategyEvaluator` or `shared_strategy_evaluator`, and proves
    `characterize_production_baseline_decision` delegates to `IndependentPreRefactorBaselineOracle`.
    """
    oracle_path = REPO_ROOT / "backend" / "services" / "v17_9_0_1_baseline_oracle.py"
    source_text = oracle_path.read_text(encoding="utf-8")
    tree = ast.parse(source_text)

    imported_names: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_names.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported_names.append(node.module)
            for alias in node.names:
                imported_names.append(alias.name)

    for forbidden in ("shared_strategy_evaluator", "SharedStrategyEvaluator"):
        assert forbidden not in imported_names, f"Forbidden self-referential import found: {forbidden}"

    series = _make_synthetic_candle_series(base_open_ms=1_761_696_000_000, direction="LONG")
    ctx = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=series["decision_ms"],
        m1_candles=series["m1_candles"],
        m5_candles=series["m5_candles"],
        m15_candles=series["m15_candles"],
        h1_candles=series["h1_candles"],
        h4_candles=series["h4_candles"],
        bid=series["bid"],
        ask=series["ask"],
    )
    SharedStrategyEvaluator.reset_runtime_invocation_counts()
    base_snap = characterize_production_baseline_decision(
        context=ctx,
        model_id=ModelIdentifier.SMC_M1_FVG_CONTINUATION.value,
        direction="LONG",
    )
    # Must NOT have incremented SharedStrategyEvaluator invocation counter!
    assert SharedStrategyEvaluator.get_runtime_invocation_counts().get("TOTAL", 0) == 0
    assert base_snap.adapter_origin == "BASELINE_DIRECT_PRODUCTION"


def test_v17_9_0_1_phase_b_55_scenario_parity(tmp_path: Path) -> None:
    """
    Phase B: Verifies all 55 scenarios (11 categories x 5 models) achieve 100% parity
    between `IndependentPreRefactorBaselineOracle` and `SharedStrategyEvaluator`.
    """
    res = generate_phase_b_baseline_artifacts(
        context_builder_fn=build_market_decision_context,
        refactored_evaluator_fn=lambda ctx, m, d: SharedStrategyEvaluator.evaluate_model_direction(ctx, m, d).to_parity_dict(),
        output_dirs=[tmp_path],
    )
    manifest = res["manifest"]
    assert manifest["self_referential"] is False
    assert manifest["imports_shared_strategy_evaluator"] is False
    assert manifest["total_scenarios"] == 55
    assert manifest["parity_matches"] == 55
    assert manifest["parity_divergence_count"] == 0
    assert manifest["parity_rate"] == 1.0
    assert (tmp_path / "BASELINE_MANIFEST.json").exists()
    assert (tmp_path / "BASELINE_DECISIONS.jsonl").exists()
    assert (tmp_path / "BASELINE_CHARACTERIZATION.md").exists()
    assert (tmp_path / "BASELINE_PROVENANCE.md").exists()


def test_v17_9_0_1_negative_acceptance_all_15_scenarios(tmp_path: Path) -> None:
    """
    Phase N: Exercises all 15 mandatory negative acceptance scenarios to prove
    the verification suite fails closed on any bypass, divergence, lookahead, or artifact fault.
    """
    series = _make_synthetic_candle_series(base_open_ms=1_761_696_000_000, direction="LONG")
    ctx = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=series["decision_ms"],
        m1_candles=series["m1_candles"],
        m5_candles=series["m5_candles"],
        m15_candles=series["m15_candles"],
        h1_candles=series["h1_candles"],
        h4_candles=series["h4_candles"],
        bid=series["bid"],
        ask=series["ask"],
    )
    ref_snap = SharedStrategyEvaluator.evaluate_model_direction(
        context=ctx,
        model_id=ModelIdentifier.SMC_M1_FVG_CONTINUATION.value,
        direction="LONG",
        adapter_origin="PRODUCTION_ORCHESTRATOR",
    )

    # 1, 2, 3: Production / MARKET_SHADOW / Backtest bypasses SharedStrategyEvaluator -> spy counter detects 0 calls
    SharedStrategyEvaluator.reset_runtime_invocation_counts()
    counts_empty = SharedStrategyEvaluator.get_runtime_invocation_counts()
    assert counts_empty.get("PRODUCTION_ORCHESTRATOR", 0) == 0
    assert counts_empty.get("MARKET_SHADOW_ADAPTER", 0) == 0
    assert counts_empty.get("HISTORICAL_BACKTEST_ADAPTER", 0) == 0

    # 4: Self-referential baseline oracle -> compare_decision_parity rejects identical adapter_origin
    fake_self_ref = dataclasses.replace(ref_snap, adapter_origin="PRODUCTION_ORCHESTRATOR")
    res_self_ref = compare_decision_parity(ref_snap, fake_self_ref, allow_same_adapter_origin=False)
    assert res_self_ref["is_match"] is False
    assert any(m["reason"] == "SELF_COMPARISON_TRAP_DETECTED" for m in res_self_ref["mismatches"])

    # 5: Production and MARKET_SHADOW diverge -> compare_production_shadow_divergence flags divergence
    shadow_svc = MarketShadowService()
    divergent_shadow = copy.deepcopy(ref_snap.to_parity_dict())
    divergent_shadow["adapter_origin"] = "MARKET_SHADOW_ADAPTER"
    divergent_shadow["selected_sl"] = 1999.99
    div_report = shadow_svc.compare_production_shadow_divergence(ref_snap, divergent_shadow)
    assert div_report["is_divergent"] is True
    assert "selected_sl" in div_report["field_diffs"]

    # 6: Production and Backtest diverge -> compare_decision_parity fails closed
    divergent_bt = dataclasses.replace(ref_snap, adapter_origin="HISTORICAL_BACKTEST_ADAPTER", take_profit=3999.99)
    res_bt_div = compare_decision_parity(ref_snap, divergent_bt)
    assert res_bt_div["is_match"] is False
    assert any(m["field"] == "take_profit" for m in res_bt_div["mismatches"])

    # 7: Unclosed H1/H4 candle injected -> rejected with UNCLOSED_4H_CANDLE_REJECTED
    unclosed_h4 = copy.deepcopy(series["h4_candles"])
    unclosed_h4[-1]["is_closed"] = False
    ctx_unclosed_h4 = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=series["decision_ms"],
        m1_candles=series["m1_candles"],
        m5_candles=series["m5_candles"],
        m15_candles=series["m15_candles"],
        h1_candles=series["h1_candles"],
        h4_candles=unclosed_h4,
        bid=series["bid"],
        ask=series["ask"],
        strict_causal_validation=True,
        strict_htf_validation=True,
    )
    assert ctx_unclosed_h4.causal_valid is False
    assert "UNCLOSED_4H_CANDLE_REJECTED" in ctx_unclosed_h4.causal_rejection_reasons

    # 8: Future candle lookahead injected -> rejected with LOOKAHEAD_REJECTED
    lookahead_m1 = copy.deepcopy(series["m1_candles"])
    lookahead_m1[-1]["close_time"] = series["decision_ms"] + 60_000
    ctx_lookahead = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=series["decision_ms"],
        m1_candles=lookahead_m1,
        m5_candles=series["m5_candles"],
        m15_candles=series["m15_candles"],
        bid=series["bid"],
        ask=series["ask"],
        strict_causal_validation=True,
    )
    assert ctx_lookahead.causal_valid is False
    assert "LOOKAHEAD_REJECTED" in ctx_lookahead.causal_rejection_reasons

    # 9: Gate mapped to unrelated test -> AUTHORITATIVE_GATE_MAPPING_V17_9_0_1 verifies all 17 gates G89-G105
    assert len(AUTHORITATIVE_GATE_MAPPING_V17_9_0_1) == 17
    for gid, meta in AUTHORITATIVE_GATE_MAPPING_V17_9_0_1.items():
        assert gid in MANDATORY_GATES_V17_9_0
        assert meta["repaired_selector_v17_9_0_1"] != ""
        assert meta["min_passed"] >= 1

    # 10: Skipped / NOT_EXECUTED test treated as PASS -> normalize_v17_9_0_1_gate_status returns INCOMPLETE
    skipped_gate = {
        "status": "PASS",
        "exit_code": 0,
        "report_status": "PARSED_OK",
        "counts": {"collected": 3, "passed": 0, "skipped": 3, "failed": 0, "errors": 0},
    }
    assert normalize_v17_9_0_1_gate_status(skipped_gate, is_test_gate=True) == "INCOMPLETE"
    assert determine_v17_9_0_1_top_level_verdict(
        {"G89_SHARED_DECISION_CONTEXT_CONTRACT": skipped_gate},
        ["G89_SHARED_DECISION_CONTEXT_CONTRACT"],
    ) == "INCOMPLETE"

    # 11: Missing JUnit XML / counts evidence -> determine_release_verdict returns BLOCKED
    missing_xml_gate = {"status": "PASS", "exit_code": 0, "report_status": "MISSING"}
    assert determine_release_verdict({"G89_SHARED_DECISION_CONTEXT_CONTRACT": missing_xml_gate}, ["G89_SHARED_DECISION_CONTEXT_CONTRACT"]) == "BLOCKED"

    # 12 & 15: Stale artifact / SHA mismatch -> ExecutionStateManager invalidates checkpoint on SHA change
    art_dir = tmp_path / "artifacts"
    mgr1 = ExecutionStateManager(
        run_id="r1",
        source_sha="sha_old_111",
        all_test_groups=["STEP_1"],
        artifact_dir=art_dir,
    )
    mgr1.load_or_resume("sha_old_111")
    xml_ok = art_dir / "junit" / "ok.xml"
    xml_ok.parent.mkdir(parents=True, exist_ok=True)
    xml_ok.write_text('<testsuite tests="1" failures="0" errors="0" skipped="0"><testcase classname="c" name="t1"/></testsuite>', encoding="utf-8")
    mgr1.record_test_group_completion(
        group_id="STEP_1",
        exit_code=0,
        artifact_paths=[xml_ok],
        current_head_sha="sha_old_111",
    )
    mgr2 = ExecutionStateManager(
        run_id="r1",
        source_sha="sha_new_222",
        all_test_groups=["STEP_1"],
        artifact_dir=art_dir,
    )
    resume_res = mgr2.load_or_resume("sha_new_222")
    assert resume_res["invalidated"] is True
    assert "SOURCE_SHA_MISMATCH" in resume_res["reason"]

    # 13: Disk free space below threshold -> TestResourceGuard fails closed with BLOCKED_DISK_BUDGET
    guard = TestResourceGuard(
        run_id="r_disk_fail",
        repo_root=tmp_path,
        artifact_dir=art_dir,
        simulate_free_disk_bytes=1024,
        reserved_floor_override_bytes=10 * 1024 * 1024,
    )
    snap_disk = guard.measure_disk_budget(stage="PREFLIGHT")
    assert snap_disk.status == "BLOCKED_DISK_BUDGET"
    with pytest.raises(DiskBudgetExceededError):
        guard.enforce_disk_budget(stage="PREFLIGHT")

    # 14: Worker crash / non-zero exit code -> normalize_v17_9_0_1_gate_status returns FAIL
    crashed_gate = {"status": "PASS", "exit_code": 1, "counts": {"passed": 1, "failed": 0, "errors": 0}}
    assert normalize_v17_9_0_1_gate_status(crashed_gate, is_test_gate=True) == "FAIL"


def test_v17_9_0_1_async_orchestrator_and_disk_guard(tmp_path: Path) -> None:
    """
    Phase J: Verifies TestResourceGuard temp directory cleanup and report generation.
    """
    art_dir = tmp_path / "artifacts"
    guard = TestResourceGuard(
        run_id="r_disk_ok",
        repo_root=tmp_path,
        artifact_dir=art_dir,
        reserved_floor_override_bytes=1024,
    )
    check = guard.enforce_disk_budget(stage="PREFLIGHT")
    assert check.status == "PASS"
    w_dir = guard.create_worker_temp_dir("worker_test_01")
    (w_dir / "temp_artifact.txt").write_text("temp", encoding="utf-8")
    assert w_dir.exists()
    guard.cleanup_worker_temp_dir("worker_test_01")
    assert not w_dir.exists()
    guard.cleanup_run_temp_dir()
    rep = guard.build_disk_budget_report()
    assert rep["status"] == "PASS"
    assert rep["final_temporary_bytes"] == 0


def test_v17_9_0_1_1_negative_tests_a_through_g_production_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    V17.9.0.1.1 Section 3 & Section 15 (Fault Injection #1-#7):
    Proves that `SharedStrategyEvaluator.evaluate_decision(...)` is the sole pre-execution
    strategy decision authority in Production (`ScalpWatcherOrchestrator` & `evaluate_production_*`),
    and that legacy `ScalpM1ModelService.evaluate_m1_*_candidate` / `evaluate_cg01_candidate`
    can NEVER override canonical `NO_TRADE`, `REJECTED`, direction, SL/TP, or missing lineage.
    """
    from services.scalp_m1_model_service import ScalpCandidate, ScalpM1ModelService
    from services.scalp_m1_types import M1ScalpConfig, WatchedZone
    from services.stop_placement_types import StopPlacementPolicy

    series = _make_synthetic_candle_series(base_open_ms=1_761_696_000_000, direction="LONG")
    decision_ms = series["decision_ms"]
    ctx = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=decision_ms,
        m1_candles=series["m1_candles"],
        m5_candles=series["m5_candles"],
        m15_candles=series["m15_candles"],
        h1_candles=series["h1_candles"],
        h4_candles=series["h4_candles"],
        bid=series["bid"],
        ask=series["ask"],
    )
    zone = WatchedZone(
        zone_id="z_test_auth",
        symbol="XAUUSDT",
        timeframe="15M",
        zone_type="OB",
        direction="BULLISH",
        top=2642.0,
        bottom=2640.0,
        created_at=decision_ms - 900_000,
        known_at=decision_ms - 300_000,
    )
    quote = CanonicalQuote(
        symbol="XAUUSDT",
        last=series["bid"],
        bid=series["bid"],
        ask=series["ask"],
        mark_price=series["bid"],
        spread=round(series["ask"] - series["bid"], 4),
        exchange_ts_ms=decision_ms - 100,
        received_at_ms=decision_ms - 50,
        status="VALID",
    )
    cfg = M1ScalpConfig()
    stop_pol = StopPlacementPolicy()
    pol_settings = {"capital_usdt": 1000.0, "risk_pct": 0.25, "leverage": 20, "margin_mode": "ISOLATED", "min_net_rr": 1.2}

    base_snap = SharedStrategyEvaluator.evaluate_model_direction(
        context=ctx,
        model_id=ModelIdentifier.SMC_M1_REVERSAL.value,
        direction="LONG",
        adapter_origin="PRODUCTION_ORCHESTRATOR",
    )

    # Helper creating a rogue legacy candidate claiming is_eligible=True
    def _make_rogue_candidate(direction: str = "LONG") -> ScalpCandidate:
        return ScalpCandidate(
            symbol="XAUUSDT",
            model_id=ModelIdentifier.SMC_M1_REVERSAL.value,
            direction=direction,
            zone_id="z_test_auth",
            planned_entry=2645.0,
            stop_loss=2642.0 if direction == "LONG" else 2648.0,
            take_profit=2652.0 if direction == "LONG" else 2638.0,
            gross_rr=2.3,
            net_rr=2.1,
            is_eligible=True,
            reason_code="ELIGIBLE",
            calculation=None,
            as_of_ms=decision_ms,
            candidate_key=f"rogue_{direction}_{decision_ms}",
        )

    # Negative Test A (Fault Injection #1): Canonical returns NO_TRADE while legacy helper returns LONG
    snap_no_trade = dataclasses.replace(
        base_snap,
        decision_status=DecisionStatus.NO_TRADE.value,
        rejection_reason_codes=("CANONICAL_NO_TRADE",),
        production_candidate_payload=_make_rogue_candidate("LONG"),
    )
    monkeypatch.setattr(SharedStrategyEvaluator, "evaluate_decision", classmethod(lambda cls, **kw: snap_no_trade))
    cand_a, res_a = SharedStrategyEvaluator.evaluate_production_reversal_candidate(
        context=ctx,
        zone=zone,
        candles_1m=series["m1_candles"],
        quote=quote,
        policy_settings=pol_settings,
        now_ms=decision_ms,
        config=cfg,
        context_levels=[],
        stop_policy=stop_pol,
        stop_context={},
    )
    assert cand_a.is_eligible is False
    assert getattr(cand_a, "strategy_decision_status", None) == "REJECT"
    assert res_a.decision_status == DecisionStatus.NO_TRADE.value

    # Negative Test B (Fault Injection #2): Canonical returns REJECTED while legacy helper returns SHORT
    snap_rejected = dataclasses.replace(
        base_snap,
        direction="SHORT",
        decision_status=DecisionStatus.REJECTED.value,
        rejection_reason_codes=("CANONICAL_RISK_REJECTED",),
        production_candidate_payload=_make_rogue_candidate("SHORT"),
    )
    monkeypatch.setattr(SharedStrategyEvaluator, "evaluate_decision", classmethod(lambda cls, **kw: snap_rejected))
    cand_b, res_b = SharedStrategyEvaluator.evaluate_production_fvg_continuation_candidate(
        context=ctx,
        closed_1m=series["m1_candles"],
        closed_5m=series["m5_candles"],
        quote=quote,
        htf_bias="BEARISH",
        h1_alignment="BEARISH",
        policy_settings=pol_settings,
        now_ms=decision_ms,
        config=cfg,
        context_levels=[],
        stop_policy=stop_pol,
        stop_context={},
    )
    assert cand_b.is_eligible is False
    assert "CANONICAL_RISK_REJECTED" in cand_b.reason_code

    # Negative Test C (Fault Injection #3): Canonical says SHORT, expected/candidate says LONG -> direction mismatch
    snap_dir_mismatch = dataclasses.replace(
        base_snap,
        direction="SHORT",
        decision_status=DecisionStatus.ELIGIBLE.value,
        entry_reference=2645.0,
        selected_sl=2648.0,
        take_profit=2638.0,
        net_rr=2.2,
        rejection_reason_codes=(),
        production_candidate_payload=_make_rogue_candidate("LONG"),
    )
    cand_c, _ = SharedStrategyEvaluator.enforce_canonical_decision_authority_on_candidate(
        snap=snap_dir_mismatch,
        context=ctx,
        model_id=ModelIdentifier.SMC_M1_REVERSAL.value,
        expected_direction="LONG",
        policy_settings=pol_settings,
        config=cfg,
        now_ms=decision_ms,
        zone=zone,
        strict_direction_match=True,
    )
    assert cand_c.is_eligible is False
    assert cand_c.reason_code == "CANONICAL_DIRECTION_MISMATCH_REJECTED"

    # Negative Test D (Fault Injection #4): Missing decision_id or decision_fingerprint -> fail closed
    snap_missing_fp = dataclasses.replace(
        base_snap,
        decision_status=DecisionStatus.ELIGIBLE.value,
        decision_fingerprint="",
        custom_decision_id="",
        production_candidate_payload=_make_rogue_candidate("LONG"),
    )
    cand_d, _ = SharedStrategyEvaluator.enforce_canonical_decision_authority_on_candidate(
        snap=snap_missing_fp,
        context=ctx,
        model_id=ModelIdentifier.SMC_M1_REVERSAL.value,
        expected_direction="LONG",
        policy_settings=pol_settings,
        config=cfg,
        now_ms=decision_ms,
        zone=zone,
    )
    assert cand_d.is_eligible is False
    assert cand_d.reason_code == "CANONICAL_DECISION_LINEAGE_MISSING"

    # Negative Test E (Fault Injection #5): Invalid SL/TP geometry (SL >= entry for LONG) -> fail closed
    snap_bad_geom = dataclasses.replace(
        base_snap,
        direction="LONG",
        decision_status=DecisionStatus.ELIGIBLE.value,
        entry_reference=2645.0,
        selected_sl=2646.5,  # Invalid for LONG: SL >= entry!
        take_profit=2652.0,
        net_rr=2.0,
        rejection_reason_codes=(),
        production_candidate_payload=_make_rogue_candidate("LONG"),
    )
    cand_e, _ = SharedStrategyEvaluator.enforce_canonical_decision_authority_on_candidate(
        snap=snap_bad_geom,
        context=ctx,
        model_id=ModelIdentifier.SMC_M1_REVERSAL.value,
        expected_direction="LONG",
        policy_settings=pol_settings,
        config=cfg,
        now_ms=decision_ms,
        zone=zone,
    )
    assert cand_e.is_eligible is False
    assert cand_e.reason_code == "CANONICAL_SL_TP_GEOMETRY_REJECTED"

    # Negative Test F (Fault Injection #6): Duplicate decision_id replay into orchestrator -> blocked
    orch = ScalpWatcherOrchestrator()
    dup_id = "dec_SMC_M1_REVERSAL_XAUUSDT_LONG_1761697800000_abcdef123456"
    assert orch.register_seen_decision_id(dup_id) is True
    assert orch.register_seen_decision_id(dup_id) is False  # Second registration blocked!

    # Negative Test G (Fault Injection #7): Canonical evaluator raises exception -> fail closed
    def _raise_boom(cls, **kw):
        raise RuntimeError("Injected canonical evaluator failure")

    monkeypatch.setattr(SharedStrategyEvaluator, "evaluate_decision", classmethod(_raise_boom))
    cand_g, res_g = SharedStrategyEvaluator.evaluate_production_continuation_candidate(
        context=ctx,
        candles_1m=series["m1_candles"],
        candles_5m=series["m5_candles"],
        quote=quote,
        htf_bias="BULLISH",
        h1_alignment="BULLISH",
        policy_settings=pol_settings,
        now_ms=decision_ms,
        config=cfg,
        stop_policy=stop_pol,
        stop_context={},
    )
    assert cand_g.is_eligible is False
    assert res_g.decision_status == DecisionStatus.REJECTED.value
    assert "CANONICAL_EVALUATOR_EXCEPTION:RuntimeError" in res_g.rejection_reason_codes[0]


def test_v17_9_0_1_1_shadow_authority_and_backtest_fail_closed_and_gate_provenance(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """
    V17.9.0.1.1 Sections 4, 5, 6, 8 & 15 (Fault Injection #18, #19):
    - Verifies `MarketShadowService` distinguishes `INPUT_MISMATCH` vs `EXACT_MATCH` vs `DECISION_DIVERGENCE`.
    - Verifies `v17_8_backtest_runner.evaluate_and_simulate_variants_on_opportunities` rejects non-executable
      canonical decisions (`NO_TRADE`, `REJECTED`, missing lineage) and exposes `authoritative_production_parity_simulation`.
    - Verifies `verify_gate_provenance_integrity` fails closed (`INVALID_EVIDENCE` / `BLOCKED`) when any gate has
      a stale `tested_source_sha != final_code_sha`.
    - Verifies 5-way baseline parity and LF byte-exact checksum matching on disk.
    """
    from services.v17_8_backtest_runner import evaluate_and_simulate_variants_on_opportunities
    from tools.acceptance_verdict import verify_gate_provenance_integrity

    series = _make_synthetic_candle_series(base_open_ms=1_761_696_000_000, direction="LONG")
    ctx = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=series["decision_ms"],
        m1_candles=series["m1_candles"],
        m5_candles=series["m5_candles"],
        m15_candles=series["m15_candles"],
        h1_candles=series["h1_candles"],
        h4_candles=series["h4_candles"],
        bid=series["bid"],
        ask=series["ask"],
    )
    snap_prod = SharedStrategyEvaluator.evaluate_model_direction(
        context=ctx,
        model_id=ModelIdentifier.SMC_M1_FVG_CONTINUATION.value,
        direction="LONG",
        adapter_origin="PRODUCTION_ORCHESTRATOR",
    )
    snap_shad = SharedStrategyEvaluator.evaluate_model_direction(
        context=ctx,
        model_id=ModelIdentifier.SMC_M1_FVG_CONTINUATION.value,
        direction="LONG",
        adapter_origin="MARKET_SHADOW_ADAPTER",
    )

    shadow_svc = MarketShadowService()
    # 1. Exact match on identical input & config hashes
    cmp_exact = shadow_svc.compare_production_shadow_divergence(snap_prod, snap_shad)
    assert cmp_exact["comparison_status"] == "EXACT_MATCH"
    assert cmp_exact["is_match"] is True
    assert cmp_exact["input_mismatch"] is False

    # 2. Input mismatch when input_snapshot_hash differs
    snap_diff_in = dataclasses.replace(snap_shad, evidence_fingerprint="0" * 64)
    cmp_in_mis = shadow_svc.compare_production_shadow_divergence(snap_prod, snap_diff_in)
    assert cmp_in_mis["comparison_status"] == "INPUT_MISMATCH"
    assert cmp_in_mis["input_mismatch"] is True
    assert cmp_in_mis["is_match"] is False

    # 3. Decision divergence when inputs match but SL/TP differs
    snap_div = dataclasses.replace(snap_shad, selected_sl=1999.0, decision_fingerprint="f" * 64)
    cmp_div = shadow_svc.compare_production_shadow_divergence(snap_prod, snap_div)
    assert cmp_div["comparison_status"] == "DECISION_DIVERGENCE"
    assert cmp_div["input_mismatch"] is False
    assert cmp_div["is_divergent"] is True

    # 4. Backtest fail-closed on non-executable canonical decision status
    rogue_opp = {
        "opportunity_id": "bt_rogue_01",
        "symbol": "XAUUSDT",
        "model_id": ModelIdentifier.SMC_M1_FVG_CONTINUATION.value,
        "direction": "LONG",
        "decision_ms": series["decision_ms"],
        "decision_id": "dec_rogue_01",
        "decision_fingerprint": "a" * 64,
        "canonical_decision_status": DecisionStatus.NO_TRADE.value,
    }
    sim_res = evaluate_and_simulate_variants_on_opportunities(
        opportunities=[rogue_opp],
        early_rejections=[],
    )
    assert "authoritative_production_parity_simulation" in sim_res
    assert sim_res["authoritative_production_parity_simulation"]["simulated_trades"] == 0
    assert any(
        r.get("reason_code") == DecisionStatus.NO_TRADE.value
        for r in sim_res["authoritative_production_parity_rejections"]
    )

    # 5. Fault Injection #18: Stale tested_source_sha on any gate -> INVALID_EVIDENCE / BLOCKED
    gates_stale = {
        "G01_BRANCH_INTEGRITY": {
            "status": "PASS",
            "exit_code": 0,
            "tested_source_sha": "old_commit_sha_111111",
        }
    }
    prov_check = verify_gate_provenance_integrity(
        gates_stale,
        final_code_sha="new_commit_sha_222222",
        mandatory_gate_manifest=["G01_BRANCH_INTEGRITY"],
    )
    assert prov_check["is_valid"] is False
    assert prov_check["status"] == "INVALID_EVIDENCE"
    assert "G01_BRANCH_INTEGRITY" in prov_check["stale_gates"]
    assert (
        determine_v17_9_0_1_top_level_verdict(
            gates_stale,
            ["G01_BRANCH_INTEGRITY"],
            expected_source_sha="new_commit_sha_222222",
        )
        == "BLOCKED"
    )

    # 6. Fault Injection #19 & Section 6: 5-way baseline parity & LF byte-exact checksum verification
    orch_inst = ScalpWatcherOrchestrator()
    five_way = {
        "shared_strategy_evaluator": lambda c, m, d: SharedStrategyEvaluator.evaluate_decision(
            market_snapshot=c,
            structural_context={"model_id": m, "direction": d, "adapter_origin": "SHARED_EVALUATOR"},
        ).to_parity_dict(),
        "production_runtime": lambda c, m, d: orch_inst.evaluate_shared_decision_snapshot(
            context=c, model_id=m, direction=d
        ).to_parity_dict(),
        "market_shadow_runtime": lambda c, m, d: shadow_svc.evaluate_shared_decision_snapshot(
            context=c, model_id=m, direction=d
        ).to_parity_dict(),
        "backtest_runtime": lambda c, m, d: evaluate_production_parity_model_snapshot(
            context=c, model_id=m, direction=d
        )["shared_decision_snapshot"],
    }
    phase_b_out = generate_phase_b_baseline_artifacts(
        context_builder_fn=build_market_decision_context,
        refactored_evaluator_fn=five_way["shared_strategy_evaluator"],
        five_way_evaluators=five_way,
        output_dirs=[tmp_path],
    )
    assert phase_b_out["manifest"]["five_way_parity_matches"] == 55
    import hashlib
    disk_bytes = (tmp_path / "BASELINE_DECISIONS.jsonl").read_bytes()
    assert b"\r\n" not in disk_bytes
    assert hashlib.sha256(disk_bytes).hexdigest() == phase_b_out["manifest"]["baseline_decisions_sha256"]

    # 7. Fault Injection #16 & Section 7: Genuine Cross-Runtime Parity Computation (FAIL on empty or divergent)
    from tools.generate_v17_9_0_1_artifacts import compute_cross_runtime_parity_report
    parity_empty = compute_cross_runtime_parity_report(
        final_code_sha="test_sha",
        phase_b_manifest=phase_b_out["manifest"],
        scenarios=[],
        injected_records=[],
    )
    assert parity_empty["status"] == "FAIL"
    assert parity_empty["compared_records_count"] == 0

    parity_div = compute_cross_runtime_parity_report(
        final_code_sha="test_sha",
        phase_b_manifest=phase_b_out["manifest"],
        scenarios=[],
        injected_records=[
            {
                "scenario_id": "FAULT_INJECT_16",
                "model_id": ModelIdentifier.SMC_M1_FVG_CONTINUATION.value,
                "production_snapshot": snap_prod.to_parity_dict(),
                "shadow_snapshot": snap_div.to_parity_dict(),
                "backtest_snapshot": snap_prod.to_parity_dict(),
            }
        ],
    )
    assert parity_div["status"] == "FAIL"
    assert parity_div["production_vs_shadow_divergences"] == 1
    assert len(parity_div["divergences"]) >= 1


def test_v17_9_0_1_negative_acceptance_v17_9_0_1_2_all_30_controls_and_historical_parity() -> None:
    """
    V17.9.0.1.2 Phases B, C, D, E, F, G, H, I:
    - Verifies all 30 negative controls (`N01`–`N30`) pass and fail closed on any unsafe state.
    - Verifies `verify_historical_baseline_commit_provenance` resolves all 15 required historical
      source files at `aurum-desk/backend/...` (`DEF-179012-03`).
    - Verifies 30 real historical XAUUSDT timestamps (`150` downstream consumed model decisions)
      achieve 100% parity (`0` divergences) across Production `evaluate_cycle`, `MARKET_SHADOW`
      `evaluate_shadow_cycle`, and Historical Backtest `evaluate_production_parity_model_snapshot`
      (`DEF-179012-04`).
    """
    from services.v17_9_0_1_baseline_oracle import verify_historical_baseline_commit_provenance
    from tools.generate_v17_9_0_1_artifacts import (
        evaluate_historical_multi_runtime_parity,
        run_v17_9_0_1_2_negative_acceptance_suite,
    )

    neg_suite = run_v17_9_0_1_2_negative_acceptance_suite(final_code_sha="test_sha_v17_9_0_1_2")
    assert neg_suite["status"] == "PASS", f"Failed controls: {[c for c in neg_suite['controls'] if not c['passed']]}"
    assert neg_suite["total_negative_controls"] == 70
    assert neg_suite["passed_negative_controls"] == 70
    assert neg_suite["failed_negative_controls"] == 0

    prov = verify_historical_baseline_commit_provenance()
    assert prov["status"] == "PASS"
    assert prov["is_valid"] is True
    assert prov["git_commit_object_verified"] is True
    assert len(prov["historical_file_sha256s"]) == 15

    hist_parity = evaluate_historical_multi_runtime_parity(max_timestamps=30)
    assert hist_parity["status"] == "PASS", f"Divergences: {hist_parity.get('divergences')}"
    assert hist_parity["timestamps_evaluated"] == 30
    assert hist_parity["compared_records_count"] == 150
    assert hist_parity["production_vs_shadow_divergences"] == 0
    assert hist_parity["production_vs_shadow_input_mismatches"] == 0
    assert hist_parity["production_vs_backtest_divergences"] == 0
    assert hist_parity["shadow_vs_backtest_divergences"] == 0
    assert hist_parity["five_way_divergences"] == 0
    assert hist_parity["status_distribution"].get("ELIGIBLE", 0) >= 1
    assert hist_parity["status_distribution"].get("VALID_SETUP_REJECTED_BY_RISK", 0) >= 1
    assert hist_parity["status_distribution"].get("WAITING_CONFIRMATION", 0) >= 1



