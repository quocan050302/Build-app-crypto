"""
Aurum Desk V17.9.0 — Dedicated Verification Suite (40 Mandatory Tests, Gates G89..G107).

Covers:
  - Section 5 & 6: Immutable MarketDecisionContext & StrategyDecisionSnapshot contracts
  - Section 7: SharedStrategyEvaluator across all 5 production models + Unicorn
  - Section 8: Stage 1 Baseline Characterization, Stage 2 Cross-Adapter Parity, and all 10 Anti-Fake Parity Controls
  - Section 9 & 14: Removal of synthetic modulo `i % 15` index signals from historical backtest
  - Section 11: Bounded Asynchronous Test Orchestrator & node accounting invariant
  - Section 12: Disk Budget Guard (`BLOCKED_DISK_BUDGET` fail-closed, worker temp cleanup, log compression)
  - Section 13: Atomic Checkpointing, Source SHA invalidation, Corrupt artifact invalidation, and Safe Resumption
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import os
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List

import pytest

from services.market_shadow_service import MarketShadowService
from services.quote_validator import CanonicalQuote
from services.scalp_m1_types import ModelIdentifier, get_scalp_config
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator
from services.shared_strategy_evaluator import (
    ALL_EVALUATED_MODEL_IDS,
    PRODUCTION_MODEL_IDS,
    REQUIRED_PARITY_FIELDS,
    VALID_DECISION_STATUSES,
    DecisionStatus,
    MarketDecisionContext,
    SharedStrategyEvaluator,
    StrategyDecisionSnapshot,
    build_market_decision_context,
    characterize_production_baseline_decision,
    compare_decision_parity,
    verify_multi_adapter_parity,
    verify_no_synthetic_index_signals_in_source,
)
from services.strategy_decision_contract import (
    MarketDecisionContext as ContractContext,
    SharedStrategyEvaluator as ContractEvaluator,
    StrategyDecisionSnapshot as ContractSnapshot,
)
from services.v17_8_backtest_runner import (
    _extract_historical_opportunities_chronologically,
    evaluate_and_simulate_variants_on_opportunities,
    evaluate_production_parity_model_snapshot,
)
from tests.test_v17_8_0_3_market_shadow import (
    _make_bullish_unicorn_m1_bars,
    _make_m5_bars,
    _make_m15_bars,
)
from tools.run_v17_9_async_regression import (
    AsyncWorkerGroupSpec,
    BoundedAsyncTestOrchestrator,
    parse_junit_xml_nodes,
)
from tools.test_resource_guard import DiskBudgetExceededError, TestResourceGuard
from tools.v17_9_execution_state import ExecutionStateManager, validate_artifact_file


REPO_ROOT = Path(__file__).resolve().parents[2]
NOW_MS = 1_793_100_000_000


def _build_standard_fixture_context(
    *,
    now_ms: int = NOW_MS,
    model_enablement: Dict[str, bool] | None = None,
) -> MarketDecisionContext:
    m1_bars = _make_bullish_unicorn_m1_bars(now_ms - 36 * 60_000, count=35)
    m5_bars = _make_m5_bars(now_ms - 17 * 300_000, count=16)
    m15_bars = _make_m15_bars(now_ms - 9 * 900_000, count=8)
    return build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=now_ms,
        m1_candles=m1_bars,
        m5_candles=m5_bars,
        m15_candles=m15_bars,
        bid=2644.00,
        ask=2644.25,
        quote_timestamp_ms=now_ms - 250,
        model_enablement=model_enablement,
    )


def _build_reversal_sweep_m1_bars(start_ms: int, count: int = 35) -> List[Dict[str, Any]]:
    bars = _make_bullish_unicorn_m1_bars(start_ms, count=count)
    # Make last bar sweep below recent swing low and reclaim strongly
    min_low = min(float(b["low"]) for b in bars[-10:-1])
    bars[-1]["open"] = round(min_low + 0.30, 2)
    bars[-1]["low"] = round(min_low - 0.45, 2)
    bars[-1]["high"] = round(min_low + 1.40, 2)
    bars[-1]["close"] = round(min_low + 1.10, 2)
    return bars


def _build_continuation_bos_m1_bars(start_ms: int, count: int = 35) -> List[Dict[str, Any]]:
    bars = _make_bullish_unicorn_m1_bars(start_ms, count=count)
    max_high = max(float(b["high"]) for b in bars[-12:-1])
    bars[-1]["open"] = round(max_high - 0.20, 2)
    bars[-1]["low"] = round(max_high - 0.35, 2)
    bars[-1]["high"] = round(max_high + 1.80, 2)
    bars[-1]["close"] = round(max_high + 1.50, 2)
    return bars


# ============================================================================
# SECTION 1: SHARED DECISION CONTRACT & FINGERPRINTS (Tests 01 - 08 -> G89, G90)
# ============================================================================


def test_01_market_decision_context_immutability_and_frozen_fields():
    ctx = _build_standard_fixture_context()
    assert ContractContext is MarketDecisionContext
    assert ContractSnapshot is StrategyDecisionSnapshot
    assert ContractEvaluator is SharedStrategyEvaluator
    assert ctx.symbol == "XAUUSDT"
    assert ctx.causal_valid is True
    assert len(ctx.closed_m1_candles) == 35
    assert len(ctx.closed_m5_candles) == 16
    assert len(ctx.source_fingerprint) == 64
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.symbol = "BTCUSDT"  # type: ignore[misc]


def test_02_market_decision_context_rejects_unclosed_m1_candles():
    m1_bars = _make_bullish_unicorn_m1_bars(NOW_MS - 36 * 60_000, count=35)
    for b in m1_bars:
        b["is_closed"] = False
    m5_bars = _make_m5_bars(NOW_MS - 17 * 300_000, count=16)
    ctx = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=NOW_MS,
        m1_candles=m1_bars,
        m5_candles=m5_bars,
        bid=2644.00,
        ask=2644.25,
        strict_causal_validation=True,
    )
    assert ctx.causal_valid is False
    assert ctx.m1_status == "UNCLOSED_REJECTED"
    assert "UNCLOSED_CANDLE" in ctx.causal_rejection_reasons
    snap = SharedStrategyEvaluator.evaluate_model_direction(
        ctx, ModelIdentifier.SMC_M1_FVG_CONTINUATION.value, "LONG"
    )
    assert snap.decision_status == DecisionStatus.MISSING_MARKET_DATA.value
    assert "UNCLOSED_CANDLE" in snap.rejection_reason_codes


def test_03_market_decision_context_rejects_unclosed_m5_candles():
    m1_bars = _make_bullish_unicorn_m1_bars(NOW_MS - 36 * 60_000, count=35)
    m5_bars = _make_m5_bars(NOW_MS - 17 * 300_000, count=16)
    for b in m5_bars:
        b["is_closed"] = False
    ctx = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=NOW_MS,
        m1_candles=m1_bars,
        m5_candles=m5_bars,
        bid=2644.00,
        ask=2644.25,
    )
    assert ctx.m5_status == "UNCLOSED_REJECTED"
    assert "UNCLOSED_M5_CANDLE_REJECTED" in ctx.causal_rejection_reasons
    snap = SharedStrategyEvaluator.evaluate_model_direction(
        ctx, ModelIdentifier.SMC_M1_FVG_CONTINUATION.value, "LONG"
    )
    assert snap.decision_status == DecisionStatus.MISSING_MARKET_DATA.value
    assert "UNCLOSED_M5_CANDLE_REJECTED" in snap.rejection_reason_codes


def test_04_market_decision_context_rejects_lookahead_future_candles():
    m1_bars = _make_bullish_unicorn_m1_bars(NOW_MS + 60_000, count=20)
    m5_bars = _make_m5_bars(NOW_MS - 17 * 300_000, count=16)
    ctx = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=NOW_MS,
        m1_candles=m1_bars,
        m5_candles=m5_bars,
        bid=2644.00,
        ask=2644.25,
        strict_causal_validation=True,
    )
    assert ctx.causal_valid is False
    assert "LOOKAHEAD_REJECTED" in ctx.causal_rejection_reasons


def test_05_market_decision_context_rejects_missing_evidence_or_invalid_quote():
    ctx = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=NOW_MS,
        m1_candles=[],
        m5_candles=[],
        bid=2645.0,
        ask=2644.0,  # inverted quote
    )
    assert ctx.causal_valid is False
    assert "MISSING_EVIDENCE" in ctx.causal_rejection_reasons
    assert "INVALID_QUOTE" in ctx.causal_rejection_reasons


def test_06_strategy_decision_snapshot_immutability_and_20_parity_fields():
    ctx = _build_standard_fixture_context()
    snap = SharedStrategyEvaluator.evaluate_model_direction(
        ctx, ModelIdentifier.SMC_M1_FVG_CONTINUATION.value, "LONG"
    )
    assert len(REQUIRED_PARITY_FIELDS) == 20
    parity_dict = snap.to_parity_dict()
    for field_name in REQUIRED_PARITY_FIELDS:
        assert field_name in parity_dict
    with pytest.raises(dataclasses.FrozenInstanceError):
        snap.selected_sl = 1000.0  # type: ignore[misc]


def test_07_strategy_decision_snapshot_deterministic_fingerprints():
    ctx1 = _build_standard_fixture_context()
    ctx2 = _build_standard_fixture_context()
    snap1 = SharedStrategyEvaluator.evaluate_model_direction(
        ctx1, ModelIdentifier.SMC_M1_FVG_CONTINUATION.value, "LONG"
    )
    snap2 = SharedStrategyEvaluator.evaluate_model_direction(
        ctx2, ModelIdentifier.SMC_M1_FVG_CONTINUATION.value, "LONG"
    )
    assert ctx1.source_fingerprint == ctx2.source_fingerprint
    assert snap1.evidence_fingerprint == snap2.evidence_fingerprint
    assert snap1.policy_fingerprint == snap2.policy_fingerprint
    assert snap1.decision_fingerprint == snap2.decision_fingerprint
    assert len(snap1.decision_fingerprint) == 64


def test_08_decision_status_enum_completeness_and_model_disabled_handling():
    expected_statuses = {
        "MODEL_DISABLED",
        "MISSING_MARKET_DATA",
        "NO_VALID_SETUP",
        "WAITING_CONFIRMATION",
        "VALID_SETUP",
        "VALID_SETUP_REJECTED_BY_RISK",
        "ELIGIBLE",
    }
    assert set(VALID_DECISION_STATUSES) == expected_statuses
    ctx_disabled = _build_standard_fixture_context(
        model_enablement={ModelIdentifier.SMC_M1_FVG_CONTINUATION.value: False}
    )
    snap = SharedStrategyEvaluator.evaluate_model_direction(
        ctx_disabled, ModelIdentifier.SMC_M1_FVG_CONTINUATION.value, "LONG"
    )
    assert snap.decision_status == DecisionStatus.MODEL_DISABLED.value
    assert "MODEL_DISABLED" in snap.rejection_reason_codes


# ============================================================================
# SECTION 2: STAGE 1 BASELINE & STAGE 2 CROSS-ADAPTER PARITY (Tests 09 - 18 -> G91..G96)
# ============================================================================


def test_09_stage1_baseline_characterization_smc_m1_reversal():
    m1_bars = _build_reversal_sweep_m1_bars(NOW_MS - 36 * 60_000, count=35)
    m5_bars = _make_m5_bars(NOW_MS - 17 * 300_000, count=16)
    ctx = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=NOW_MS,
        m1_candles=m1_bars,
        m5_candles=m5_bars,
        bid=2644.00,
        ask=2644.25,
    )
    base = characterize_production_baseline_decision(
        ctx, ModelIdentifier.SMC_M1_REVERSAL.value, "LONG"
    )
    shared = SharedStrategyEvaluator.evaluate_model_direction(
        ctx, ModelIdentifier.SMC_M1_REVERSAL.value, "LONG"
    )
    diff = compare_decision_parity(base, shared)
    assert diff["is_match"] is True
    assert base.setup_validity == "VALID"


def test_10_stage1_baseline_characterization_smc_m1_continuation():
    m1_bars = _build_continuation_bos_m1_bars(NOW_MS - 36 * 60_000, count=35)
    m5_bars = _make_m5_bars(NOW_MS - 17 * 300_000, count=16)
    ctx = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=NOW_MS,
        m1_candles=m1_bars,
        m5_candles=m5_bars,
        bid=2645.00,
        ask=2645.25,
    )
    base = characterize_production_baseline_decision(
        ctx, ModelIdentifier.SMC_M1_CONTINUATION.value, "LONG"
    )
    shared = SharedStrategyEvaluator.evaluate_model_direction(
        ctx, ModelIdentifier.SMC_M1_CONTINUATION.value, "LONG"
    )
    diff = compare_decision_parity(base, shared)
    assert diff["is_match"] is True
    assert base.setup_validity == "VALID"


def test_11_stage1_baseline_characterization_smc_m1_fvg_continuation():
    ctx = _build_standard_fixture_context()
    base = characterize_production_baseline_decision(
        ctx, ModelIdentifier.SMC_M1_FVG_CONTINUATION.value, "LONG"
    )
    shared = SharedStrategyEvaluator.evaluate_model_direction(
        ctx, ModelIdentifier.SMC_M1_FVG_CONTINUATION.value, "LONG"
    )
    diff = compare_decision_parity(base, shared)
    assert diff["is_match"] is True
    assert base.setup_validity == "VALID"
    assert base.unicorn_validity in ("VALID", "VALID_UNICORN")


def test_12_stage1_baseline_characterization_smc_m1_sweep_reclaim():
    m1_bars = _build_reversal_sweep_m1_bars(NOW_MS - 36 * 60_000, count=35)
    m5_bars = _make_m5_bars(NOW_MS - 17 * 300_000, count=16)
    ctx = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=NOW_MS,
        m1_candles=m1_bars,
        m5_candles=m5_bars,
        bid=2644.00,
        ask=2644.25,
    )
    base = characterize_production_baseline_decision(
        ctx, ModelIdentifier.SMC_M1_SWEEP_RECLAIM.value, "LONG"
    )
    shared = SharedStrategyEvaluator.evaluate_model_direction(
        ctx, ModelIdentifier.SMC_M1_SWEEP_RECLAIM.value, "LONG"
    )
    diff = compare_decision_parity(base, shared)
    assert diff["is_match"] is True
    assert base.setup_validity == "VALID"


def test_13_stage1_baseline_characterization_cg01_sweep_mss_fvg():
    ctx = _build_standard_fixture_context()
    base = characterize_production_baseline_decision(
        ctx, ModelIdentifier.CG01_SWEEP_MSS_FVG.value, "LONG"
    )
    shared = SharedStrategyEvaluator.evaluate_model_direction(
        ctx, ModelIdentifier.CG01_SWEEP_MSS_FVG.value, "LONG"
    )
    diff = compare_decision_parity(base, shared)
    assert diff["is_match"] is True
    assert base.setup_validity == "VALID"


def test_14_stage2_parity_production_vs_shared_all_5_models():
    ctx = _build_standard_fixture_context()
    orch = ScalpWatcherOrchestrator()
    for mid in PRODUCTION_MODEL_IDS:
        shared_snap = SharedStrategyEvaluator.evaluate_model_direction(
            ctx, mid, "LONG", adapter_origin="SHARED_EVALUATOR"
        )
        prod_snap = orch.evaluate_shared_decision_snapshot(
            context=ctx, model_id=mid, direction="LONG"
        )
        assert prod_snap.adapter_origin == "PRODUCTION_ORCHESTRATOR_ADAPTER"
        res = compare_decision_parity(shared_snap, prod_snap)
        assert res["is_match"] is True, f"Mismatch on {mid}: {res}"


def test_15_stage2_parity_shadow_vs_shared_all_5_models():
    ctx = _build_standard_fixture_context()
    shadow_svc = MarketShadowService(symbol="XAUUSDT", storage=None)
    for mid in PRODUCTION_MODEL_IDS:
        shared_snap = SharedStrategyEvaluator.evaluate_model_direction(
            ctx, mid, "LONG", adapter_origin="SHARED_EVALUATOR"
        )
        sh_snap = shadow_svc.evaluate_shared_decision_snapshot(
            context=ctx, model_id=mid, direction="LONG"
        )
        assert sh_snap.adapter_origin == "MARKET_SHADOW_ADAPTER"
        res = compare_decision_parity(shared_snap, sh_snap)
        assert res["is_match"] is True, f"Mismatch on {mid}: {res}"


def test_16_stage2_parity_backtest_vs_shared_all_5_models():
    m1_bars = _make_bullish_unicorn_m1_bars(NOW_MS - 36 * 60_000, count=35)
    m5_bars = _make_m5_bars(NOW_MS - 17 * 300_000, count=16)
    m15_bars = _make_m15_bars(NOW_MS - 9 * 900_000, count=8)
    quote = CanonicalQuote(
        symbol="XAUUSDT",
        last=2644.10,
        bid=2644.00,
        ask=2644.25,
        mark_price=2644.10,
        spread=0.25,
        exchange_ts_ms=NOW_MS - 250,
        received_at_ms=NOW_MS - 200,
        status="VALID",
    )
    ctx = _build_standard_fixture_context()
    for mid in PRODUCTION_MODEL_IDS:
        shared_snap = SharedStrategyEvaluator.evaluate_model_direction(
            ctx, mid, "LONG", adapter_origin="SHARED_EVALUATOR"
        )
        bt_out = evaluate_production_parity_model_snapshot(
            symbol="XAUUSDT",
            model_id=mid,
            closed_1m=m1_bars,
            closed_5m=m5_bars,
            closed_15m=m15_bars,
            quote=quote,
            as_of_ms=NOW_MS,
            direction="LONG",
        )
        assert bt_out["adapter_origin"] == "HISTORICAL_BACKTEST_ADAPTER"
        assert bt_out["decision_fingerprint"] == shared_snap.decision_fingerprint


def test_17_stage2_multi_adapter_parity_verification_valid_unicorn_setup():
    ctx = _build_standard_fixture_context()
    mid = ModelIdentifier.SMC_M1_FVG_CONTINUATION.value
    orch = ScalpWatcherOrchestrator()
    shadow_svc = MarketShadowService(symbol="XAUUSDT", storage=None)

    base_snap = characterize_production_baseline_decision(ctx, mid, "LONG")
    shared_snap = SharedStrategyEvaluator.evaluate_model_direction(
        ctx, mid, "LONG", adapter_origin="SHARED_EVALUATOR"
    )
    prod_snap = orch.evaluate_shared_decision_snapshot(context=ctx, model_id=mid, direction="LONG")
    sh_snap = shadow_svc.evaluate_shared_decision_snapshot(context=ctx, model_id=mid, direction="LONG")
    bt_snap = SharedStrategyEvaluator.evaluate_model_direction(
        ctx, mid, "LONG", adapter_origin="HISTORICAL_BACKTEST_ADAPTER"
    )

    multi = verify_multi_adapter_parity(
        baseline_snapshot=base_snap,
        shared_snapshot=shared_snap,
        production_snapshot=prod_snap,
        shadow_snapshot=sh_snap,
        backtest_snapshot=bt_snap,
    )
    assert multi["is_match"] is True
    assert multi["parity_status"] == "PARITY_MATCH"
    assert multi["total_mismatch_count"] == 0


def test_18_stage2_multi_adapter_parity_verification_no_valid_setup_and_rejected_setup():
    ctx = _build_standard_fixture_context()
    mid = ModelIdentifier.SMC_M1_CONTINUATION.value  # No BOS on the FVG retest bar
    orch = ScalpWatcherOrchestrator()
    shadow_svc = MarketShadowService(symbol="XAUUSDT", storage=None)

    base_snap = characterize_production_baseline_decision(ctx, mid, "LONG")
    shared_snap = SharedStrategyEvaluator.evaluate_model_direction(
        ctx, mid, "LONG", adapter_origin="SHARED_EVALUATOR"
    )
    prod_snap = orch.evaluate_shared_decision_snapshot(context=ctx, model_id=mid, direction="LONG")
    sh_snap = shadow_svc.evaluate_shared_decision_snapshot(context=ctx, model_id=mid, direction="LONG")
    bt_snap = SharedStrategyEvaluator.evaluate_model_direction(
        ctx, mid, "LONG", adapter_origin="HISTORICAL_BACKTEST_ADAPTER"
    )

    assert base_snap.setup_validity == "INVALID"
    multi = verify_multi_adapter_parity(
        baseline_snapshot=base_snap,
        shared_snapshot=shared_snap,
        production_snapshot=prod_snap,
        shadow_snapshot=sh_snap,
        backtest_snapshot=bt_snap,
    )
    assert multi["is_match"] is True


# ============================================================================
# SECTION 3: 10 MANDATORY ANTI-FAKE PARITY CONTROLS (Tests 19 - 28 -> G97)
# ============================================================================


def test_19_anti_fake_control_01_self_comparing_shadow_wrapper_fails_parity():
    ctx = _build_standard_fixture_context()
    shadow_svc = MarketShadowService(symbol="XAUUSDT", storage=None)
    s1 = shadow_svc.evaluate_shared_decision_snapshot(
        context=ctx, model_id=ModelIdentifier.SMC_M1_FVG_CONTINUATION.value, direction="LONG"
    )
    s2 = shadow_svc.evaluate_shared_decision_snapshot(
        context=ctx, model_id=ModelIdentifier.SMC_M1_FVG_CONTINUATION.value, direction="LONG"
    )
    diff = compare_decision_parity(s1, s2, allow_same_adapter_origin=False)
    assert diff["is_match"] is False
    assert diff["parity_status"] == "PRODUCTION_PARITY_MISMATCH"
    assert any(m["reason"] == "SELF_COMPARISON_TRAP_DETECTED" for m in diff["mismatches"])


def test_20_anti_fake_control_02_identical_adapter_origin_rejected_as_self_comparison():
    ctx = _build_standard_fixture_context()
    snap = SharedStrategyEvaluator.evaluate_model_direction(
        ctx, ModelIdentifier.SMC_M1_FVG_CONTINUATION.value, "LONG", adapter_origin="SHARED_EVALUATOR"
    )
    diff = compare_decision_parity(snap, snap, allow_same_adapter_origin=False)
    assert diff["is_match"] is False
    assert diff["parity_status"] == "PRODUCTION_PARITY_MISMATCH"


def test_21_anti_fake_control_03_synthetic_modulo_15_index_signal_rejected_by_ast_auditor(tmp_path: Path):
    bad_file = tmp_path / "synthetic_modulo.py"
    bad_file.write_text("def run(n):\n    for i in range(n):\n        if i % 15 == 0:\n            pass\n", encoding="utf-8")
    res = verify_no_synthetic_index_signals_in_source([bad_file])
    assert res["is_clean"] is False
    assert res["status"] == "PRODUCTION_PARITY_MISMATCH"
    assert res["violation_count"] >= 1


def test_22_anti_fake_control_04_synthetic_modulo_alternating_model_rejected_by_ast_auditor(tmp_path: Path):
    bad_file = tmp_path / "synthetic_alt.py"
    bad_file.write_text(
        "def pick(i):\n    return 'A' if (i // 15) % 2 == 0 else 'B'\n",
        encoding="utf-8",
    )
    res = verify_no_synthetic_index_signals_in_source([bad_file])
    assert res["is_clean"] is False
    assert res["status"] == "PRODUCTION_PARITY_MISMATCH"


def test_23_anti_fake_control_05_divergent_selected_sl_fails_parity():
    ctx = _build_standard_fixture_context()
    s1 = SharedStrategyEvaluator.evaluate_model_direction(
        ctx, ModelIdentifier.SMC_M1_FVG_CONTINUATION.value, "LONG", adapter_origin="SHARED_EVALUATOR"
    )
    s2 = replace(
        s1,
        selected_sl=(s1.selected_sl or 2640.0) - 0.50,
        adapter_origin="MUTATED_ADAPTER",
    )
    diff = compare_decision_parity(s1, s2)
    assert diff["is_match"] is False
    assert diff["parity_status"] == "PRODUCTION_PARITY_MISMATCH"
    assert any(m["field"] == "selected_sl" for m in diff["mismatches"])


def test_24_anti_fake_control_06_divergent_take_profit_fails_parity():
    ctx = _build_standard_fixture_context()
    s1 = SharedStrategyEvaluator.evaluate_model_direction(
        ctx, ModelIdentifier.SMC_M1_FVG_CONTINUATION.value, "LONG", adapter_origin="SHARED_EVALUATOR"
    )
    s2 = replace(
        s1,
        take_profit=(s1.take_profit or 2650.0) + 1.25,
        adapter_origin="MUTATED_ADAPTER",
    )
    diff = compare_decision_parity(s1, s2)
    assert diff["is_match"] is False
    assert any(m["field"] == "take_profit" for m in diff["mismatches"])


def test_25_anti_fake_control_07_divergent_unicorn_validity_fails_parity():
    ctx = _build_standard_fixture_context()
    s1 = SharedStrategyEvaluator.evaluate_model_direction(
        ctx, ModelIdentifier.SMC_M1_FVG_CONTINUATION.value, "LONG", adapter_origin="SHARED_EVALUATOR"
    )
    s2 = replace(
        s1,
        unicorn_validity="INVALID_CAUSAL_ORDER",
        adapter_origin="MUTATED_ADAPTER",
    )
    diff = compare_decision_parity(s1, s2)
    assert diff["is_match"] is False
    assert any(m["field"] == "unicorn_validity" for m in diff["mismatches"])


def test_26_anti_fake_control_08_divergent_selected_anchor_id_fails_parity():
    ctx = _build_standard_fixture_context()
    s1 = SharedStrategyEvaluator.evaluate_model_direction(
        ctx, ModelIdentifier.SMC_M1_FVG_CONTINUATION.value, "LONG", adapter_origin="SHARED_EVALUATOR"
    )
    s2 = replace(
        s1,
        selected_anchor_id="mutated_anchor_999",
        adapter_origin="MUTATED_ADAPTER",
    )
    diff = compare_decision_parity(s1, s2)
    assert diff["is_match"] is False
    assert any(m["field"] == "selected_anchor_id" for m in diff["mismatches"])


def test_27_anti_fake_control_09_divergent_hypothetical_quantity_or_net_rr_fails_parity():
    ctx = _build_standard_fixture_context()
    s1 = SharedStrategyEvaluator.evaluate_model_direction(
        ctx, ModelIdentifier.SMC_M1_FVG_CONTINUATION.value, "LONG", adapter_origin="SHARED_EVALUATOR"
    )
    s2 = replace(
        s1,
        hypothetical_quantity=(s1.hypothetical_quantity or 1.0) + 0.25,
        net_rr=(s1.net_rr or 2.0) + 0.5,
        adapter_origin="MUTATED_ADAPTER",
    )
    diff = compare_decision_parity(s1, s2)
    assert diff["is_match"] is False
    fields = {m["field"] for m in diff["mismatches"]}
    assert "hypothetical_quantity" in fields
    assert "net_rr" in fields


def test_28_anti_fake_control_10_divergent_rejection_reason_codes_or_decision_status_fails_parity():
    ctx = _build_standard_fixture_context()
    s1 = SharedStrategyEvaluator.evaluate_model_direction(
        ctx, ModelIdentifier.SMC_M1_FVG_CONTINUATION.value, "LONG", adapter_origin="SHARED_EVALUATOR"
    )
    s2 = replace(
        s1,
        decision_status=DecisionStatus.NO_VALID_SETUP.value,
        rejection_reason_codes=("SHADOW_REJECTED_INSUFFICIENT_NET_RR",),
        adapter_origin="MUTATED_ADAPTER",
    )
    diff = compare_decision_parity(s1, s2)
    assert diff["is_match"] is False
    fields = {m["field"] for m in diff["mismatches"]}
    assert "decision_status" in fields
    assert "rejection_reason_codes" in fields


# ============================================================================
# SECTION 4: HISTORICAL BACKTEST PARITY & ZERO INDEX SIGNALS (Tests 29 - 32 -> G98..G100)
# ============================================================================


def test_29_v17_8_backtest_runner_source_has_zero_synthetic_modulo_signals():
    audit = verify_no_synthetic_index_signals_in_source()
    assert audit["is_clean"] is True, f"Synthetic index signals found: {audit}"
    assert audit["status"] == "CLEAN"
    assert audit["violation_count"] == 0


def test_30_historical_opportunity_extraction_driven_by_causal_market_structure_only():
    m1_bars = _make_bullish_unicorn_m1_bars(NOW_MS - 80 * 60_000, count=75)
    m5_bars = _make_m5_bars(NOW_MS - 30 * 300_000, count=28)
    m15_bars = _make_m15_bars(NOW_MS - 15 * 900_000, count=14)
    datasets = {"1m": m1_bars, "5m": m5_bars, "15m": m15_bars}
    opps, _ = _extract_historical_opportunities_chronologically(datasets)
    assert isinstance(opps, list)
    for opp in opps:
        assert opp["model_id"] in PRODUCTION_MODEL_IDS
        assert opp["decision_ms"] <= NOW_MS


def test_31_per_model_backtest_breakdown_covers_all_5_production_models():
    model_results_path = REPO_ROOT / "backtest" / "matched_comparison.json"
    if model_results_path.exists():
        data = json.loads(model_results_path.read_text(encoding="utf-8"))
        per_model = data.get("per_model_breakdown", {})
        for mid in PRODUCTION_MODEL_IDS:
            assert mid in per_model


def test_32_matched_cohort_and_full_strategy_denominators_separate_and_reproducible():
    m1_bars = _make_bullish_unicorn_m1_bars(NOW_MS - 80 * 60_000, count=75)
    m5_bars = _make_m5_bars(NOW_MS - 30 * 300_000, count=28)
    m15_bars = _make_m15_bars(NOW_MS - 15 * 900_000, count=14)
    datasets = {"1m": m1_bars, "5m": m5_bars, "15m": m15_bars}
    opps, _ = _extract_historical_opportunities_chronologically(datasets)
    res1 = evaluate_and_simulate_variants_on_opportunities(opps)
    res2 = evaluate_and_simulate_variants_on_opportunities(opps)
    assert res1["variants"]["C"]["net_pnl"] == res2["variants"]["C"]["net_pnl"]
    assert "matched_A_vs_C_cohort" in res1["matched_cohort_analysis"]
    assert "matched_all_ABCD_cohort" in res1["matched_cohort_analysis"]


# ============================================================================
# SECTION 5: ASYNC ORCHESTRATOR, DISK GUARD & CHECKPOINTS (Tests 33 - 40 -> G101..G107)
# ============================================================================


def test_33_disk_budget_guard_preflight_pass_and_worker_temp_cleanup(tmp_path: Path):
    guard = TestResourceGuard(
        run_id="test_run_33",
        repo_root=tmp_path,
        artifact_dir=tmp_path / "artifacts",
        reserved_floor_override_bytes=1024 * 1024,  # 1 MiB
    )
    snap = guard.enforce_disk_budget(stage="PREFLIGHT")
    assert snap.status == "PASS"
    wdir = guard.create_worker_temp_dir("GROUP_A")
    assert wdir.exists()
    (wdir / "scratch.txt").write_text("temp", encoding="utf-8")
    cleaned = guard.cleanup_worker_temp_dir("GROUP_A")
    assert cleaned is True
    assert not wdir.exists()
    report = guard.build_disk_budget_report()
    assert report["status"] == "PASS"
    assert report["final_temporary_bytes"] == 0


def test_34_disk_budget_guard_fails_closed_with_blocked_disk_budget_when_below_floor(tmp_path: Path):
    guard = TestResourceGuard(
        run_id="test_run_34",
        repo_root=tmp_path,
        artifact_dir=tmp_path / "artifacts",
        reserved_floor_override_bytes=10 * 1024 * 1024 * 1024,  # 10 GiB floor
        simulate_free_disk_bytes=512 * 1024 * 1024,  # 512 MiB simulated free
    )
    with pytest.raises(DiskBudgetExceededError) as exc_info:
        guard.enforce_disk_budget(stage="PREFLIGHT_LOW_DISK")
    assert exc_info.value.verdict == "BLOCKED_DISK_BUDGET"
    assert exc_info.value.report["status"] == "BLOCKED_DISK_BUDGET"


def test_35_large_log_compression_preserves_uncompressed_sha256_and_tail_summary(tmp_path: Path):
    guard = TestResourceGuard(
        run_id="test_run_35",
        repo_root=tmp_path,
        artifact_dir=tmp_path / "artifacts",
        reserved_floor_override_bytes=1024,
    )
    log_path = tmp_path / "artifacts" / "logs" / "large.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    raw_content = ("LINE_CONTENT_FOR_COMPRESSION_TEST\n" * 400).encode("utf-8")
    expected_sha = hashlib.sha256(raw_content).hexdigest()
    log_path.write_bytes(raw_content)

    meta = guard.compress_large_log_if_needed(log_path, threshold_bytes=1024, tail_lines=10)
    assert meta is not None
    assert meta["uncompressed_sha256"] == expected_sha
    assert Path(meta["gz_log"]).exists()
    tail_text = log_path.read_text(encoding="utf-8")
    assert expected_sha in tail_text


def test_36_execution_state_atomic_checkpoint_and_verified_resumption(tmp_path: Path):
    art_dir = tmp_path / "artifacts"
    mgr = ExecutionStateManager(
        run_id="test_run_36",
        source_sha="sha_abc123",
        all_test_groups=["GROUP_A", "GROUP_B"],
        artifact_dir=art_dir,
    )
    init_res = mgr.load_or_resume("sha_abc123")
    assert init_res["resumed"] is False

    junit_a = art_dir / "junit" / "group_a.xml"
    junit_a.parent.mkdir(parents=True, exist_ok=True)
    junit_a.write_text('<testsuite tests="1"><testcase classname="t" name="a"/></testsuite>', encoding="utf-8")

    ok, reason = mgr.record_test_group_completion(
        group_id="GROUP_A",
        exit_code=0,
        artifact_paths=[junit_a],
        current_head_sha="sha_abc123",
    )
    assert ok is True
    assert reason == "COMPLETED"

    # Resume with same SHA
    mgr2 = ExecutionStateManager(
        run_id="test_run_36",
        source_sha="sha_abc123",
        all_test_groups=["GROUP_A", "GROUP_B"],
        artifact_dir=art_dir,
    )
    resumed = mgr2.load_or_resume("sha_abc123")
    assert resumed["resumed"] is True
    assert resumed["completed_test_groups"] == ["GROUP_A"]
    assert resumed["pending_test_groups"] == ["GROUP_B"]
    assert resumed["resume_count"] == 1


def test_37_execution_state_invalidates_checkpoint_on_source_sha_change(tmp_path: Path):
    art_dir = tmp_path / "artifacts"
    mgr = ExecutionStateManager(
        run_id="test_run_37",
        source_sha="sha_old",
        all_test_groups=["GROUP_A", "GROUP_B"],
        artifact_dir=art_dir,
    )
    mgr.load_or_resume("sha_old")
    junit_a = art_dir / "junit" / "group_a.xml"
    junit_a.parent.mkdir(parents=True, exist_ok=True)
    junit_a.write_text('<testsuite tests="1"><testcase classname="t" name="a"/></testsuite>', encoding="utf-8")
    mgr.record_test_group_completion(
        group_id="GROUP_A",
        exit_code=0,
        artifact_paths=[junit_a],
        current_head_sha="sha_old",
    )

    # Resume with NEW SHA -> must invalidate previous completed groups
    mgr_new = ExecutionStateManager(
        run_id="test_run_37",
        source_sha="sha_new",
        all_test_groups=["GROUP_A", "GROUP_B"],
        artifact_dir=art_dir,
    )
    res = mgr_new.load_or_resume("sha_new")
    assert res["invalidated"] is True
    assert res["reason"] == "CHECKPOINT_INVALIDATED_SOURCE_SHA_MISMATCH"
    assert res["completed_test_groups"] == []
    assert res["pending_test_groups"] == ["GROUP_A", "GROUP_B"]


def test_38_execution_state_invalidates_corrupt_or_missing_junit_xml_on_resume(tmp_path: Path):
    art_dir = tmp_path / "artifacts"
    mgr = ExecutionStateManager(
        run_id="test_run_38",
        source_sha="sha_same",
        all_test_groups=["GROUP_A"],
        artifact_dir=art_dir,
    )
    mgr.load_or_resume("sha_same")
    junit_a = art_dir / "junit" / "group_a.xml"
    junit_a.parent.mkdir(parents=True, exist_ok=True)
    junit_a.write_text('<testsuite tests="1"><testcase classname="t" name="a"/></testsuite>', encoding="utf-8")
    mgr.record_test_group_completion(
        group_id="GROUP_A",
        exit_code=0,
        artifact_paths=[junit_a],
        current_head_sha="sha_same",
    )

    # Corrupt the JUnit XML file
    junit_a.write_text("<testsuite><unclosed_tag>", encoding="utf-8")
    mgr2 = ExecutionStateManager(
        run_id="test_run_38",
        source_sha="sha_same",
        all_test_groups=["GROUP_A"],
        artifact_dir=art_dir,
    )
    res = mgr2.load_or_resume("sha_same")
    assert res["invalidated"] is True
    assert "GROUP_A" in res["invalidated_groups"]
    assert res["completed_test_groups"] == []
    assert res["pending_test_groups"] == ["GROUP_A"]


def test_39_bounded_async_test_orchestrator_parallel_groups_and_node_accounting_invariant(tmp_path: Path):
    art_dir = tmp_path / "artifacts"
    guard = TestResourceGuard(
        run_id="test_run_39",
        repo_root=tmp_path,
        artifact_dir=art_dir,
        reserved_floor_override_bytes=1024,
    )
    state_mgr = ExecutionStateManager(
        run_id="test_run_39",
        source_sha="sha_39",
        all_test_groups=["GROUP_A", "GROUP_B"],
        artifact_dir=art_dir,
    )
    orch = BoundedAsyncTestOrchestrator(
        run_id="test_run_39",
        source_sha="sha_39",
        artifact_dir=art_dir,
        resource_guard=guard,
        state_manager=state_mgr,
        max_pytest_workers=2,
    )

    specs = []
    for gid in ("GROUP_A", "GROUP_B"):
        xml_out = art_dir / "junit" / f"{gid}.xml"
        log_out = art_dir / "logs" / f"{gid}.log"
        cmd = [
            sys.executable,
            "-c",
            (
                f"from pathlib import Path; "
                f"p = Path(r'{xml_out}'); p.parent.mkdir(parents=True, exist_ok=True); "
                f"p.write_text('<testsuite tests=\"2\"><testcase classname=\"{gid}\" name=\"t1\"/><testcase classname=\"{gid}\" name=\"t2\"/></testsuite>', encoding='utf-8'); "
                f"print('{gid}_OK')"
            ),
        ]
        specs.append(
            AsyncWorkerGroupSpec(
                group_id=gid,
                description=f"Test {gid}",
                command=cmd,
                cwd=tmp_path,
                junit_xml_path=xml_out,
                log_path=log_out,
                worker_pool="PYTEST",
            )
        )

    report = asyncio.run(orch.run_groups(specs))
    assert report["status"] == "PASS"
    assert report["node_accounting_invariant_holds"] is True
    assert report["total_expected_nodes"] == 4
    assert report["total_completed_nodes"] == 4
    assert report["total_failed_nodes"] == 0


def test_40_runtime_safety_market_shadow_zero_execution_and_operational_db_untouched():
    op_db = REPO_ROOT / "backend" / "aurum_desk.db"
    before_mtime = op_db.stat().st_mtime_ns if op_db.exists() else None

    ctx = _build_standard_fixture_context()
    snaps = SharedStrategyEvaluator.evaluate_all_models(ctx)
    assert len(snaps) == 5
    for s in snaps:
        assert s.model_id in PRODUCTION_MODEL_IDS

    after_mtime = op_db.stat().st_mtime_ns if op_db.exists() else None
    assert before_mtime == after_mtime
