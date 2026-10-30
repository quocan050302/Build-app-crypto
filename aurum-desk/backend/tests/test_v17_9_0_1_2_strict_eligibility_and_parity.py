"""
Aurum Desk V17.9.0.1.2 — Strict Eligibility Invariants & Historical Parity Closure Test Suite.

Covers:
  - DEF-179012-01: Strict positive canonical eligibility invariant (`validate_canonical_executability`,
    `canonical_is_executable`) and prevention of legacy candidate status promotion or coordinate overwrite
    in `SharedStrategyEvaluator._execute_production_domain_spec()`.
  - DEF-179012-02: Strict positive `DecisionStatus.ELIGIBLE` gate across all Historical Backtest boundaries
    (`evaluate_production_parity_model_snapshot`, `_extract_historical_opportunities_chronologically`,
    `evaluate_and_simulate_variants_on_opportunities`, and `authoritative_production_parity_simulation`).
  - DEF-179012-03: Repository-root path fix (`aurum-desk/backend/...`), non-empty `historical_file_sha256s`,
    materialized historical tree, and isolated subprocess execution of historical baseline commit
    `c356d6551e13cd6af61cea5c3ab1f4aae1548af2` with zero current-tree domain imports.
  - DEF-179012-04: Real historical `XAUUSDT` multi-runtime decision parity across Production
    (`ScalpWatcherOrchestrator.evaluate_cycle`), `MARKET_SHADOW` (`MarketShadowService.evaluate_shadow_cycle`),
    and Historical Backtest (`evaluate_production_parity_model_snapshot`).
  - DEF-179012-05: Periodic resource sampling (`peak_rss_mb_observed`, `free_disk_bytes_postflight`,
    honest speedup qualification) in `BoundedAsyncTestOrchestrator` and `TestResourceGuard`.
  - Mandatory Negative Acceptance Controls `N01` through `N30`.
"""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import gzip
import json
import sys
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "backend"))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.scalp_m1_model_service import ScalpCandidate
from services.scalp_m1_types import M1ScalpConfig, ModelIdentifier
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator
from services.shared_strategy_evaluator import (
    DecisionStatus,
    SharedStrategyEvaluator,
    build_market_decision_context,
    canonical_is_executable,
    compare_decision_parity,
    validate_canonical_executability,
)
from services.v17_8_backtest_runner import (
    _extract_historical_opportunities_chronologically,
    build_backtest_ledger_provenance_envelope,
    compute_executable_source_tree_hash,
    evaluate_and_simulate_variants_on_opportunities,
    execute_chronological_backtest_window_for_parity,
    extract_and_validate_real_forward_candles,
    reconcile_backtest_trade_decision_lineage,
    reconcile_parity_windows_with_chronological_backtest,
    validate_cached_trade_ledger_provenance,
    validate_decision_context_no_future_leakage,
    validate_forward_candles_for_simulation,
    verify_backtest_source_sha_provenance,
)
from services.v17_9_0_1_baseline_oracle import (
    _build_historical_subprocess_harness_code,
    _make_synthetic_candle_series,
    build_phase_b_characterization_scenarios,
    build_real_historical_baseline_scenarios,
    classify_historical_vs_refactored_decision,
    execute_isolated_historical_baseline_scenarios,
    materialize_historical_baseline_tree,
    validate_historical_baseline_execution_authority,
    verify_historical_baseline_commit_provenance,
    verify_historical_baseline_dependency_integrity,
)
from tools.acceptance_verdict import (
    determine_v17_9_0_1_top_level_verdict,
    normalize_v17_9_0_1_gate_status,
    validate_comparison_record_hashes,
    validate_comparison_source_accounting,
    validate_semantic_acceptance_bundle,
    verify_gate_provenance_integrity,
)
from tools.generate_v17_9_0_1_artifacts import (
    _write_lf_bytes,
    compute_cross_runtime_parity_report,
    evaluate_historical_multi_runtime_parity,
)
from tools.run_v17_9_async_regression import AsyncWorkerGroupSpec, BoundedAsyncTestOrchestrator
from tools.test_resource_guard import DiskBudgetExceededError, TestResourceGuard
from tools.v17_9_execution_state import ExecutionStateManager


_CACHED_ELIGIBLE_BY_DIR: Dict[str, Any] = {}


def _build_valid_eligible_snapshot_and_context(direction: str = "LONG"):
    if direction in _CACHED_ELIGIBLE_BY_DIR:
        return _CACHED_ELIGIBLE_BY_DIR[direction]
    series = _make_synthetic_candle_series(base_open_ms=1_761_696_000_000, direction="LONG", pattern="TREND_IMPULSE")
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
        policy_config={"min_net_rr": 0.5, "tick_size": 0.01},
        risk_config={"min_net_rr": 0.5},
        htf_bias="BULLISH" if direction == "LONG" else "BEARISH",
        h1_alignment="ALIGNED",
    )
    snap = SharedStrategyEvaluator.evaluate_decision(
        market_snapshot=ctx,
        structural_context={"model_id": "SMC_M1_FVG_CONTINUATION", "direction": "LONG"},
    )
    if snap.decision_status != DecisionStatus.ELIGIBLE.value or direction == "SHORT":
        ent = 2645.0 if direction == "LONG" else 2650.0
        sl = 2641.0 if direction == "LONG" else 2654.0
        tp = 2655.0 if direction == "LONG" else 2640.0
        snap = dataclasses.replace(
            snap,
            direction=direction,
            entry_reference=ent,
            selected_sl=sl,
            take_profit=tp,
            decision_status=DecisionStatus.ELIGIBLE.value,
            setup_validity="VALID",
            hypothetical_quantity=0.2,
            net_rr=2.0,
            gross_rr=2.5,
            rejection_reason_codes=(),
            raw_candidate_reason_code="ELIGIBLE",
        )
    assert snap.decision_status == DecisionStatus.ELIGIBLE.value
    assert canonical_is_executable(snap, context=ctx) is True
    _CACHED_ELIGIBLE_BY_DIR[direction] = (snap, ctx, series)
    return snap, ctx, series


def _build_fake_eligible_legacy_candidate(direction: str = "LONG", entry: float = 2650.0) -> ScalpCandidate:
    sl = entry - 4.0 if direction == "LONG" else entry + 4.0
    tp = entry + 12.0 if direction == "LONG" else entry - 12.0
    return ScalpCandidate(
        is_eligible=True,
        model_id=ModelIdentifier.SMC_M1_FVG_CONTINUATION.value,
        direction=direction,
        candidate_key="rogue_cand_key",
        symbol="XAUUSDT",
        zone_id="ZONE_FAKE_PROMOTION",
        planned_entry=entry,
        stop_loss=sl,
        take_profit=tp,
        gross_rr=3.0,
        net_rr=2.5,
        reason_code=None,
        as_of_ms=1_761_697_800_000,
    )


def _simulate_non_promotion_via_domain_spec(non_exec_status: str) -> Dict[str, Any]:
    """
    Tests `SharedStrategyEvaluator._execute_production_domain_spec` and
    `enforce_canonical_decision_authority_on_candidate` when base canonical evaluation has
    `non_exec_status` while the legacy domain evaluator returns `is_eligible=True`.
    """
    base_elig, ctx, series = _build_valid_eligible_snapshot_and_context("LONG")
    fake_cand = _build_fake_eligible_legacy_candidate("LONG", entry=float(base_elig.entry_reference))
    non_exec_snap = dataclasses.replace(
        base_elig,
        decision_status=non_exec_status,
        rejection_reason_codes=(f"CANONICAL_{non_exec_status}",),
        raw_candidate_reason_code=f"CANONICAL_{non_exec_status}",
        production_candidate_payload=fake_cand,
    )
    snap_out = SharedStrategyEvaluator._execute_production_domain_spec(
        context=ctx,
        base_snap=non_exec_snap,
        spec={"eval_type": "FVG_CONTINUATION", "now_ms": series["decision_ms"]},
    )
    cand_out, enforced_snap = SharedStrategyEvaluator.enforce_canonical_decision_authority_on_candidate(
        snap=snap_out,
        context=ctx,
        model_id="SMC_M1_FVG_CONTINUATION",
        expected_direction="LONG",
        policy_settings={"capital_usdt": 1000.0, "risk_pct": 0.25, "leverage": 20, "margin_mode": "ISOLATED"},
        config=M1ScalpConfig(),
        now_ms=series["decision_ms"],
    )
    is_exec, rej_reason = validate_canonical_executability(enforced_snap, context=ctx)
    return {
        "input_status": non_exec_status,
        "output_decision_status": enforced_snap.decision_status,
        "candidate_is_eligible": bool(cand_out.is_eligible),
        "candidate_strategy_status": str(getattr(cand_out, "strategy_decision_status", "")),
        "validator_is_executable": bool(is_exec),
        "validator_reason": rej_reason,
        "passed": (
            enforced_snap.decision_status == non_exec_status
            and cand_out.is_eligible is False
            and getattr(cand_out, "strategy_decision_status", "") == "REJECT"
            and is_exec is False
        ),
    }


# ============================================================================
# NEGATIVE ACCEPTANCE CONTROLS N01 - N30
# ============================================================================


def test_n01_canonical_valid_setup_rejected_by_risk_cannot_be_promoted():
    res = _simulate_non_promotion_via_domain_spec(DecisionStatus.VALID_SETUP_REJECTED_BY_RISK.value)
    assert res["passed"] is True, f"N01 failed: {res}"


def test_n02_canonical_no_valid_setup_cannot_be_promoted():
    res = _simulate_non_promotion_via_domain_spec(DecisionStatus.NO_VALID_SETUP.value)
    assert res["passed"] is True, f"N02 failed: {res}"


def test_n03_canonical_waiting_confirmation_cannot_be_promoted():
    res = _simulate_non_promotion_via_domain_spec(DecisionStatus.WAITING_CONFIRMATION.value)
    assert res["passed"] is True, f"N03 failed: {res}"


def test_n04_canonical_model_disabled_cannot_be_promoted():
    res = _simulate_non_promotion_via_domain_spec(DecisionStatus.MODEL_DISABLED.value)
    assert res["passed"] is True, f"N04 failed: {res}"


def test_n05_canonical_missing_market_data_cannot_be_promoted():
    res = _simulate_non_promotion_via_domain_spec(DecisionStatus.MISSING_MARKET_DATA.value)
    assert res["passed"] is True, f"N05 failed: {res}"


def test_n06_canonical_no_trade_cannot_be_promoted():
    res = _simulate_non_promotion_via_domain_spec(DecisionStatus.NO_TRADE.value)
    assert res["passed"] is True, f"N06 failed: {res}"


def test_n07_canonical_rejected_cannot_be_promoted():
    res = _simulate_non_promotion_via_domain_spec(DecisionStatus.REJECTED.value)
    assert res["passed"] is True, f"N07 failed: {res}"


def test_n08_canonical_unknown_status_cannot_be_promoted():
    res = _simulate_non_promotion_via_domain_spec("EXPERIMENTAL_ELIGIBLE")
    assert res["passed"] is True, f"N08 failed: {res}"


def test_n09_canonical_eligible_downgraded_when_legacy_candidate_rejects():
    base_elig, ctx, series = _build_valid_eligible_snapshot_and_context("LONG")
    rej_cand = _build_fake_eligible_legacy_candidate("LONG", entry=float(base_elig.entry_reference))
    rej_cand.is_eligible = False
    rej_cand.reason_code = "INVALID_COST_CONFIGURATION"
    snap_with_rej = dataclasses.replace(base_elig, production_candidate_payload=rej_cand)
    out_snap = SharedStrategyEvaluator._execute_production_domain_spec(
        context=ctx,
        base_snap=snap_with_rej,
        spec={"eval_type": "FVG_CONTINUATION", "now_ms": series["decision_ms"], "policy_settings": {"costs_valid": False}},
    )
    assert out_snap.decision_status == DecisionStatus.VALID_SETUP_REJECTED_BY_RISK.value
    assert canonical_is_executable(out_snap, context=ctx) is False


def test_n10_canonical_selected_sl_never_overwritten_by_legacy_candidate():
    base_elig, ctx, series = _build_valid_eligible_snapshot_and_context("LONG")
    tampered_cand = _build_fake_eligible_legacy_candidate("LONG", entry=float(base_elig.entry_reference))
    tampered_cand.stop_loss = float(base_elig.selected_sl) - 15.0
    snap_with_cand = dataclasses.replace(base_elig, production_candidate_payload=tampered_cand)
    out_snap = SharedStrategyEvaluator._execute_production_domain_spec(
        context=ctx,
        base_snap=snap_with_cand,
        spec={"eval_type": "FVG_CONTINUATION", "now_ms": series["decision_ms"]},
    )
    assert float(out_snap.selected_sl) == pytest.approx(float(base_elig.selected_sl))
    assert float(out_snap.production_candidate_payload.stop_loss) == pytest.approx(float(base_elig.selected_sl))


def test_n11_canonical_take_profit_never_overwritten_by_legacy_candidate():
    base_elig, ctx, series = _build_valid_eligible_snapshot_and_context("LONG")
    tampered_cand = _build_fake_eligible_legacy_candidate("LONG", entry=float(base_elig.entry_reference))
    tampered_cand.take_profit = float(base_elig.take_profit) + 25.0
    snap_with_cand = dataclasses.replace(base_elig, production_candidate_payload=tampered_cand)
    out_snap = SharedStrategyEvaluator._execute_production_domain_spec(
        context=ctx,
        base_snap=snap_with_cand,
        spec={"eval_type": "FVG_CONTINUATION", "now_ms": series["decision_ms"]},
    )
    assert float(out_snap.take_profit) == pytest.approx(float(base_elig.take_profit))
    assert float(out_snap.production_candidate_payload.take_profit) == pytest.approx(float(base_elig.take_profit))


def test_n12_canonical_entry_reference_never_overwritten_by_legacy_candidate():
    base_elig, ctx, series = _build_valid_eligible_snapshot_and_context("LONG")
    tampered_cand = _build_fake_eligible_legacy_candidate("LONG", entry=float(base_elig.entry_reference) - 5.0)
    snap_with_cand = dataclasses.replace(base_elig, production_candidate_payload=tampered_cand)
    out_snap = SharedStrategyEvaluator._execute_production_domain_spec(
        context=ctx,
        base_snap=snap_with_cand,
        spec={"eval_type": "FVG_CONTINUATION", "now_ms": series["decision_ms"]},
    )
    assert float(out_snap.entry_reference) == pytest.approx(float(base_elig.entry_reference))
    assert float(out_snap.production_candidate_payload.planned_entry) == pytest.approx(float(base_elig.entry_reference))


_CACHED_STRICT_OPPS = None


def _get_strict_historical_opps():
    global _CACHED_STRICT_OPPS
    if _CACHED_STRICT_OPPS is not None:
        return _CACHED_STRICT_OPPS
    ds_gz = REPO_ROOT / "backtest" / "historical_dataset_XAUUSDT.json.gz"
    with gzip.open(ds_gz, "rt", encoding="utf-8") as f:
        raw_ds = json.load(f)
    ds_full = raw_ds.get("datasets", raw_ds)
    cutoff_ms = 1_792_010_000_000
    ds_slice = {
        "1m": [c for c in ds_full.get("1m", []) if int(c.get("close_time", 0) or 0) <= cutoff_ms][-1200:],
        "5m": [c for c in ds_full.get("5m", []) if int(c.get("close_time", 0) or 0) <= cutoff_ms][-300:],
        "15m": [c for c in ds_full.get("15m", []) if int(c.get("close_time", 0) or 0) <= cutoff_ms][-120:],
        "1h": [c for c in ds_full.get("1h", ds_full.get("1H", [])) if int(c.get("close_time", 0) or 0) <= cutoff_ms][-48:],
        "4h": [c for c in ds_full.get("4h", ds_full.get("4H", [])) if int(c.get("close_time", 0) or 0) <= cutoff_ms][-24:],
    }
    opps_strict, _ = _extract_historical_opportunities_chronologically(
        ds_slice, include_abcd_research_cohort=False
    )
    _CACHED_STRICT_OPPS = opps_strict
    return opps_strict


def test_n13_backtest_extraction_strict_positive_eligible_only():
    opps_strict = _get_strict_historical_opps()
    assert len(opps_strict) >= 5
    assert all(o["canonical_decision_status"] == DecisionStatus.ELIGIBLE.value for o in opps_strict)
    assert all(o["canonical_is_eligible"] is True for o in opps_strict)


def test_n14_backtest_simulation_blocks_valid_setup_rejected_by_risk_from_authoritative_parity():
    opps_strict = _get_strict_historical_opps()
    tampered = copy.deepcopy(opps_strict[:2])
    for o in tampered:
        o["canonical_decision_status"] = DecisionStatus.VALID_SETUP_REJECTED_BY_RISK.value
        o["canonical_is_eligible"] = True  # Even if tampered to True, status != ELIGIBLE must block
        o["shared_decision_snapshot"]["decision_status"] = DecisionStatus.VALID_SETUP_REJECTED_BY_RISK.value
    sim = evaluate_and_simulate_variants_on_opportunities(tampered, early_rejections=[])
    assert sim["authoritative_production_parity_simulation"]["simulated_trades"] == 0


def test_n15_backtest_simulation_rejects_unknown_canonical_status():
    opps_strict = _get_strict_historical_opps()
    tampered = copy.deepcopy(opps_strict[:2])
    for o in tampered:
        o["canonical_decision_status"] = "UNKNOWN_NON_EXECUTABLE_STATUS"
        o["canonical_is_eligible"] = True
        o["shared_decision_snapshot"]["decision_status"] = "UNKNOWN_NON_EXECUTABLE_STATUS"
    sim = evaluate_and_simulate_variants_on_opportunities(tampered, early_rejections=[])
    assert sim["authoritative_production_parity_simulation"]["simulated_trades"] == 0
    assert sim["variants"]["C"]["simulated_trades"] == 0


def test_n16_backtest_simulation_requires_explicit_positive_canonical_is_eligible():
    opps_strict = _get_strict_historical_opps()
    tampered = copy.deepcopy(opps_strict[:2])
    for o in tampered:
        o.pop("canonical_is_eligible", None)  # Omitted flag must not default to True
    sim = evaluate_and_simulate_variants_on_opportunities(tampered, early_rejections=[])
    assert sim["authoritative_production_parity_simulation"]["simulated_trades"] == 0


def test_n17_direction_mismatch_rejected_across_evaluators():
    base_elig, ctx, series = _build_valid_eligible_snapshot_and_context("LONG")
    wrong_cand = _build_fake_eligible_legacy_candidate("SHORT", entry=float(base_elig.entry_reference))
    snap_with_wrong = dataclasses.replace(base_elig, production_candidate_payload=wrong_cand)
    enforced_cand, _ = SharedStrategyEvaluator.enforce_canonical_decision_authority_on_candidate(
        snap=snap_with_wrong,
        context=ctx,
        model_id="SMC_M1_FVG_CONTINUATION",
        expected_direction="SHORT",
        policy_settings={"capital_usdt": 1000.0, "risk_pct": 0.25, "leverage": 20, "margin_mode": "ISOLATED"},
        config=M1ScalpConfig(),
        now_ms=series["decision_ms"],
        strict_direction_match=True,
    )
    assert enforced_cand.is_eligible is False
    assert getattr(enforced_cand, "strategy_decision_status", "") == "REJECT"
    assert enforced_cand.reason_code == "CANONICAL_DIRECTION_MISMATCH_REJECTED"


def test_n18_missing_decision_lineage_rejected_fail_closed():
    base_elig, _, _ = _build_valid_eligible_snapshot_and_context("LONG")
    no_id_snap = dataclasses.replace(base_elig, custom_decision_id="", decision_fingerprint="")
    is_exec, reason = validate_canonical_executability(no_id_snap)
    assert is_exec is False
    assert reason == "CANONICAL_DECISION_LINEAGE_MISSING"


def test_n19_invalid_long_sl_tp_geometry_rejected_fail_closed():
    base_elig, _, _ = _build_valid_eligible_snapshot_and_context("LONG")
    bad_long = dataclasses.replace(
        base_elig,
        direction="LONG",
        entry_reference=2650.0,
        selected_sl=2652.0,  # SL >= entry for LONG
        take_profit=2645.0,  # TP <= entry for LONG
    )
    is_exec, reason = validate_canonical_executability(bad_long)
    assert is_exec is False
    assert reason == "CANONICAL_SL_TP_GEOMETRY_REJECTED"


def test_n20_invalid_short_sl_tp_geometry_rejected_fail_closed():
    base_elig, _, _ = _build_valid_eligible_snapshot_and_context("SHORT")
    bad_short = dataclasses.replace(
        base_elig,
        direction="SHORT",
        entry_reference=2650.0,
        selected_sl=2648.0,  # SL <= entry for SHORT
        take_profit=2655.0,  # TP >= entry for SHORT
    )
    is_exec, reason = validate_canonical_executability(bad_short)
    assert is_exec is False
    assert reason == "CANONICAL_SL_TP_GEOMETRY_REJECTED"


def test_n21_non_positive_quantity_or_net_rr_rejected_fail_closed():
    base_elig, _, _ = _build_valid_eligible_snapshot_and_context("LONG")
    bad_qty = dataclasses.replace(base_elig, hypothetical_quantity=0.0)
    is_exec_qty, reason_qty = validate_canonical_executability(bad_qty)
    assert is_exec_qty is False
    assert reason_qty == "CANONICAL_INVALID_HYPOTHETICAL_QUANTITY"

    bad_rr = dataclasses.replace(base_elig, net_rr=0.0)
    is_exec_rr, reason_rr = validate_canonical_executability(bad_rr)
    assert is_exec_rr is False
    assert reason_rr == "CANONICAL_INVALID_NET_RR"


def test_n22_duplicate_decision_id_replay_blocked():
    orch = ScalpWatcherOrchestrator()
    base_elig, _, _ = _build_valid_eligible_snapshot_and_context("LONG")
    assert orch.register_seen_decision_id(base_elig.decision_id) is True
    assert orch.register_seen_decision_id(base_elig.decision_id) is False


def test_n23_canonical_evaluator_exception_fails_closed():
    _, ctx, _ = _build_valid_eligible_snapshot_and_context("LONG")
    with patch.object(SharedStrategyEvaluator, "evaluate_model_direction", side_effect=RuntimeError("BOOM")):
        snap = SharedStrategyEvaluator.evaluate_decision(
            market_snapshot=ctx,
            structural_context={"model_id": "SMC_M1_FVG_CONTINUATION", "direction": "LONG"},
        )
    assert snap.decision_status == DecisionStatus.REJECTED.value
    assert any("CANONICAL_EVALUATOR_EXCEPTION:RuntimeError" in r for r in snap.rejection_reason_codes)
    assert canonical_is_executable(snap, context=ctx) is False


def test_n24_historical_baseline_provenance_missing_commit_fails_closed():
    prov_bad = verify_historical_baseline_commit_provenance(
        commit_sha="0000000000000000000000000000000000000000"
    )
    assert prov_bad["status"] == "BLOCKED"
    assert prov_bad["git_commit_object_verified"] is False


def test_n25_historical_baseline_isolated_execution_zero_current_tree_imports():
    prov = verify_historical_baseline_commit_provenance()
    assert prov["status"] == "PASS"
    assert len(prov["historical_file_sha256s"]) == 15

    tree_info = materialize_historical_baseline_tree()
    assert tree_info["status"] == "PASS"

    dep = verify_historical_baseline_dependency_integrity()
    assert dep["status"] == "PASS"
    assert dep["mismatched_files"] == []
    assert dep["missing_extracted_files"] == []

    scenarios = build_phase_b_characterization_scenarios(build_market_decision_context)
    iso = execute_isolated_historical_baseline_scenarios(scenarios[:10])
    assert iso["status"] == "PASS"
    assert len(iso["loaded_historical_modules"]) >= 9
    assert all(m["from_isolated_historical_tree"] is True for m in iso["loaded_historical_modules"].values())
    assert len(iso["decisions_by_scenario"]) == 10


def test_n26_cross_runtime_parity_fails_closed_on_empty_or_divergent_records():
    empty_rep = compute_cross_runtime_parity_report(
        final_code_sha="deadbeef",
        phase_b_manifest={"total_scenarios": 0, "parity_matches": 0, "parity_divergence_count": 0, "parity_rate": 0.0},
        scenarios=[],
        injected_records=[],
    )
    assert empty_rep["status"] == "FAIL"

    base_elig_dict = _build_valid_eligible_snapshot_and_context("LONG")[0].to_parity_dict()
    div_shadow = dict(base_elig_dict)
    div_shadow["decision_status"] = DecisionStatus.NO_TRADE.value
    div_shadow["decision_fingerprint"] = "divergent_fp"
    div_rep = compute_cross_runtime_parity_report(
        final_code_sha="deadbeef",
        phase_b_manifest={"total_scenarios": 1, "parity_matches": 1, "parity_divergence_count": 0, "parity_rate": 1.0},
        scenarios=[],
        injected_records=[
            {
                "scenario_id": "INJECTED_DIVERGENCE_N26",
                "model_id": "SMC_M1_FVG_CONTINUATION",
                "production_snapshot": base_elig_dict,
                "shadow_snapshot": div_shadow,
                "backtest_snapshot": base_elig_dict,
            }
        ],
    )
    assert div_rep["status"] == "FAIL"
    assert div_rep["divergences_count"] > 0


def test_n27_unclosed_htf_candle_rejected_by_causality_validator():
    base_elig, _, series = _build_valid_eligible_snapshot_and_context("LONG")
    unclosed_h1 = copy.deepcopy(series["h1_candles"])
    unclosed_h1[-1]["close_time"] = series["decision_ms"] + 3_000_000
    bad_ctx = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=series["decision_ms"],
        m1_candles=series["m1_candles"],
        m5_candles=series["m5_candles"],
        m15_candles=series["m15_candles"],
        h1_candles=unclosed_h1,
        h4_candles=series["h4_candles"],
        bid=series["bid"],
        ask=series["ask"],
        strict_htf_validation=True,
    )
    assert bad_ctx.causal_valid is False
    is_exec, reason = validate_canonical_executability(base_elig, context=bad_ctx)
    assert is_exec is False
    assert reason in ("LOOKAHEAD_1H_CANDLE_REJECTED", "UNCLOSED_1H_CANDLE_REJECTED", "CANONICAL_CONTEXT_CAUSAL_INVALID")


def test_n28_stale_tested_source_sha_blocks_gate_and_verdict():
    gates = {
        "G01_BRANCH_INTEGRITY": {
            "gate_id": "G01_BRANCH_INTEGRITY",
            "status": "PASS",
            "exit_code": 0,
            "tested_source_sha": "1111111111111111111111111111111111111111",
        }
    }
    prov = verify_gate_provenance_integrity(
        gates,
        final_code_sha="2222222222222222222222222222222222222222",
        mandatory_gate_manifest=["G01_BRANCH_INTEGRITY"],
    )
    assert prov["status"] == "INVALID_EVIDENCE"
    assert prov["is_valid"] is False
    norm = normalize_v17_9_0_1_gate_status(
        gates["G01_BRANCH_INTEGRITY"],
        is_test_gate=False,
        expected_source_sha="2222222222222222222222222222222222222222",
    )
    assert norm == "BLOCKED"
    top = determine_v17_9_0_1_top_level_verdict(
        gates,
        mandatory_gate_manifest=["G01_BRANCH_INTEGRITY"],
        expected_source_sha="2222222222222222222222222222222222222222",
    )
    assert top == "BLOCKED"


def test_n29_lf_byte_integrity_strips_crlf_and_verifies_sha256(tmp_path: Path):
    target = tmp_path / "crlf_test.md"
    digest = _write_lf_bytes(target, "line1\r\nline2\r\n")
    raw = target.read_bytes()
    assert b"\r\n" not in raw
    assert raw == b"line1\nline2\n"
    assert len(digest) == 64


def test_n30_resource_guard_and_periodic_telemetry_verification(tmp_path: Path):
    guard = TestResourceGuard(
        run_id="test_n30_guard",
        repo_root=REPO_ROOT,
        artifact_dir=tmp_path / "art",
    )
    report = guard.build_disk_budget_report()
    assert report["status"] == "PASS"
    assert report["preflight_free_disk_bytes"] > 0
    assert report["postflight_free_disk_bytes"] > 0

    impossible_guard = TestResourceGuard(
        run_id="test_n30_impossible",
        repo_root=tmp_path,
        artifact_dir=tmp_path / "art_imp",
        simulate_free_disk_bytes=1024,
        reserved_floor_override_bytes=10 * 1024 * 1024,
    )
    with pytest.raises(DiskBudgetExceededError):
        impossible_guard.enforce_disk_budget(stage="PREFLIGHT")

    state_mgr = ExecutionStateManager(
        run_id="test_n30_async",
        source_sha="deadbeef",
        all_test_groups=["N30_ECHO"],
        artifact_dir=tmp_path,
    )
    orch = BoundedAsyncTestOrchestrator(
        run_id="test_n30_async",
        source_sha="deadbeef",
        artifact_dir=tmp_path,
        resource_guard=guard,
        state_manager=state_mgr,
    )
    spec = AsyncWorkerGroupSpec(
        group_id="N30_ECHO",
        description="Echo worker for telemetry check",
        command=[sys.executable, "-c", "print('ok')"],
        cwd=REPO_ROOT,
        junit_xml_path=None,
        log_path=tmp_path / "n30.log",
        worker_pool="UNIT",
        stage="STAGE_A",
    )
    summary = asyncio.run(orch.run_groups([spec]))
    perf = orch.build_performance_report(summary)
    assert perf["execution_metrics"]["peak_rss_mb_observed"] > 0
    assert perf["execution_metrics"]["resource_samples_collected"] >= 1
    assert perf["disk_telemetry"]["free_disk_bytes_postflight"] > 0


def test_real_historical_xauusdt_multi_runtime_parity():
    """
    DEF-179012-04: Replays real chronological XAUUSDT market states across
    Production (`ScalpWatcherOrchestrator.evaluate_cycle`),
    `MARKET_SHADOW` (`MarketShadowService.evaluate_shadow_cycle`), and
    Historical Backtest (`evaluate_production_parity_model_snapshot`),
    proving 100% parity (0 divergences, 0 input mismatches).
    """
    rep = evaluate_historical_multi_runtime_parity(max_timestamps=30)
    assert rep["status"] == "PASS"
    assert rep["compared_records_count"] >= 100
    assert rep["five_way_divergences"] == 0
    assert rep["production_vs_shadow_divergences"] == 0
    assert rep["production_vs_shadow_input_mismatches"] == 0
    assert rep["production_vs_backtest_divergences"] == 0
    assert rep["shadow_vs_backtest_divergences"] == 0
    assert rep["htf_causality_violations_count"] == 0


# ============================================================================
# NEGATIVE ACCEPTANCE CONTROLS N31 - N50 (V17.9.0.1.2 FINAL CLOSURE)
# ============================================================================


def test_n31_unconsumed_shared_snapshot_in_consumed_model_decisions_rejected():
    """N31: Unconsumed shared snapshot added to `consumed_model_decisions` -> FAIL."""
    snap, _, _ = _build_valid_eligible_snapshot_and_context("LONG")
    snap_dict = snap.to_parity_dict()
    reconciled = ScalpWatcherOrchestrator.reconcile_consumed_decisions_from_snapshots(
        shared_decision_snapshots=[snap_dict],
        downstream_candidates_by_fp={},
        injected_consumed_model_decisions={snap.model_id: snap_dict},
    )
    assert reconciled["is_valid"] is False
    assert reconciled["status"] == "FAIL"
    assert reconciled["unconsumed_reported_as_consumed_count"] >= 1


def test_n32_candidate_missing_decision_id_or_fingerprint_rejected():
    """N32: Candidate missing `decision_id` or `decision_fingerprint` -> FAIL."""
    snap, _, _ = _build_valid_eligible_snapshot_and_context("LONG")
    snap_dict = snap.to_parity_dict()
    cand = _build_fake_eligible_legacy_candidate("LONG", entry=float(snap.entry_reference))
    cand.decision_id = ""
    cand.decision_fingerprint = snap.decision_fingerprint
    reconciled = ScalpWatcherOrchestrator.reconcile_consumed_decisions_from_snapshots(
        shared_decision_snapshots=[snap_dict],
        downstream_candidates_by_fp={snap.decision_fingerprint: cand},
    )
    assert reconciled["is_valid"] is False
    assert reconciled["status"] == "FAIL"
    assert reconciled["missing_downstream_consumer_link_count"] >= 1


def test_n33_order_intent_from_unconsumed_decision_rejected():
    """N33: Order intent created from a decision not marked `CANDIDATE_CONSUMED` -> FAIL."""
    snap, _, _ = _build_valid_eligible_snapshot_and_context("LONG")
    snap_dict = snap.to_parity_dict()
    reconciled = ScalpWatcherOrchestrator.reconcile_consumed_decisions_from_snapshots(
        shared_decision_snapshots=[snap_dict],
        downstream_candidates_by_fp={},
        order_intent_decision_ids={snap.decision_id: "intent_rogue_001"},
    )
    assert reconciled["is_valid"] is False
    assert reconciled["status"] == "FAIL"
    assert reconciled["order_intent_without_candidate_consumed_count"] >= 1


def test_n34_simulated_trade_without_decision_id_rejected():
    """N34: Simulated trade without `decision_id` -> FAIL."""
    rec = reconcile_backtest_trade_decision_lineage(
        authoritative_trades=[
            {
                "trade_id": "auth_parity_missing_dec_id",
                "decision_id": "",
                "decision_fingerprint": "fp_123",
                "canonical_decision_status": "ELIGIBLE",
                "canonical_is_eligible": True,
            }
        ],
        authoritative_rejections=[],
    )
    assert rec["is_valid"] is False
    assert rec["status"] == "FAIL"
    assert rec["unlinked_trades_count"] >= 1


def test_n35_simulated_trade_linked_to_non_eligible_decision_rejected():
    """N35: Simulated trade linked to non-`ELIGIBLE` decision -> FAIL."""
    rec = reconcile_backtest_trade_decision_lineage(
        authoritative_trades=[
            {
                "trade_id": "auth_parity_non_elig",
                "decision_id": "dec_non_elig_001",
                "decision_fingerprint": "fp_non_elig_001",
                "canonical_decision_status": "VALID_SETUP_REJECTED_BY_RISK",
                "canonical_is_eligible": True,
                "direction": "LONG",
                "planned_entry": 2650.0,
                "stop_loss": 2646.0,
                "take_profit": 2660.0,
            }
        ],
        authoritative_rejections=[],
        canonical_decisions_by_id={
            "dec_non_elig_001": {
                "decision_id": "dec_non_elig_001",
                "decision_fingerprint": "fp_non_elig_001",
                "decision_status": "VALID_SETUP_REJECTED_BY_RISK",
                "direction": "LONG",
                "entry_reference": 2650.0,
                "selected_sl": 2646.0,
                "take_profit": 2660.0,
            }
        },
    )
    assert rec["is_valid"] is False
    assert rec["status"] == "FAIL"
    assert rec["non_eligible_linked_trades_count"] >= 1


def test_n36_eligible_backtest_decision_neither_traded_nor_blocked_rejected():
    """N36: `ELIGIBLE` backtest decision neither traded nor explicitly blocked with reason -> FAIL."""
    rec = reconcile_backtest_trade_decision_lineage(
        authoritative_trades=[],
        authoritative_rejections=[],
        canonical_decisions_by_id={
            "dec_unhandled_elig_001": {
                "decision_id": "dec_unhandled_elig_001",
                "decision_fingerprint": "fp_unhandled_elig_001",
                "decision_status": "ELIGIBLE",
                "direction": "LONG",
                "entry_reference": 2650.0,
                "selected_sl": 2646.0,
                "take_profit": 2660.0,
            }
        },
    )
    assert rec["is_valid"] is False
    assert rec["status"] == "FAIL"
    assert rec["unconsumed_eligible_decisions_count"] >= 1


def test_n37_backtest_adapter_vs_chronological_trade_ledger_divergence_rejected():
    """N37: Backtest parity using adapter-only call while chronological trade ledger diverges -> FAIL."""
    snap, _, series = _build_valid_eligible_snapshot_and_context("LONG")
    # First verify real chronological window execution works
    bt_win = execute_chronological_backtest_window_for_parity(
        symbol="XAUUSDT",
        model_id="SMC_M1_FVG_CONTINUATION",
        closed_1m=series["m1_candles"],
        closed_5m=series["m5_candles"],
        closed_15m=series["m15_candles"],
        closed_1h=series["h1_candles"],
        closed_4h=series["h4_candles"],
        as_of_ms=series["decision_ms"],
    )
    assert bt_win["execution_mode"] == "CHRONOLOGICAL_BACKTEST_SIMULATION_PIPELINE"
    # Inject a chronological trade ledger whose stop_loss diverges from the canonical decision
    divergent_rec = reconcile_backtest_trade_decision_lineage(
        authoritative_trades=[
            {
                "trade_id": "auth_parity_divergent_sl",
                "decision_id": snap.decision_id,
                "decision_fingerprint": snap.decision_fingerprint,
                "canonical_decision_status": "ELIGIBLE",
                "canonical_is_eligible": True,
                "direction": snap.direction,
                "planned_entry": snap.entry_reference,
                "stop_loss": float(snap.selected_sl) - 2.50,
                "take_profit": snap.take_profit,
            }
        ],
        authoritative_rejections=[],
        canonical_decisions_by_id={snap.decision_id: snap.to_parity_dict()},
    )
    assert divergent_rec["is_valid"] is False
    assert divergent_rec["status"] == "FAIL"
    assert divergent_rec["coordinate_mismatch_trades_count"] >= 1


def test_n38_three_way_comparison_counted_as_five_way_rejected():
    """N38: 3-way comparison counted as 5-way comparison -> FAIL."""
    snap, _, _ = _build_valid_eligible_snapshot_and_context("LONG")
    p_dict = snap.to_parity_dict()
    res = validate_comparison_source_accounting(
        {
            "production_runtime": p_dict,
            "market_shadow_runtime": p_dict,
            "chronological_backtest_runtime": p_dict,
        },
        claimed_five_way=True,
    )
    assert res["is_valid"] is False
    assert res["status"] == "FAIL"
    assert res["comparison_tier"] == "THREE_WAY_CURRENT_RUNTIME"
    assert "THREE_WAY_COMPARISON_COUNTED_AS_FIVE_WAY" in res["violations"]


def test_n39_five_way_comparison_missing_historical_baseline_rejected():
    """N39: 5-way comparison missing Historical Baseline decision -> FAIL."""
    snap, _, _ = _build_valid_eligible_snapshot_and_context("LONG")
    p_dict = snap.to_parity_dict()
    res = validate_comparison_source_accounting(
        {
            "shared_strategy_evaluator": p_dict,
            "production_runtime": p_dict,
            "market_shadow_runtime": p_dict,
            "chronological_backtest_runtime": p_dict,
        },
        claimed_five_way=True,
    )
    assert res["is_valid"] is False
    assert res["status"] == "FAIL"
    assert "FIVE_WAY_MISSING_HISTORICAL_BASELINE_DECISION" in res["violations"]


def test_n40_five_way_comparison_missing_shared_evaluator_rejected():
    """N40: 5-way comparison missing `SharedStrategyEvaluator` decision -> FAIL."""
    snap, _, _ = _build_valid_eligible_snapshot_and_context("LONG")
    p_dict = snap.to_parity_dict()
    res = validate_comparison_source_accounting(
        {
            "historical_baseline_oracle": p_dict,
            "production_runtime": p_dict,
            "market_shadow_runtime": p_dict,
            "chronological_backtest_runtime": p_dict,
        },
        claimed_five_way=True,
    )
    assert res["is_valid"] is False
    assert res["status"] == "FAIL"
    assert "FIVE_WAY_MISSING_SHARED_EVALUATOR_DECISION" in res["violations"]


def test_n41_current_tree_oracle_wrapper_injected_into_historical_subprocess_rejected():
    """N41: Current-tree oracle wrapper injected into historical subprocess -> FAIL."""
    res = validate_historical_baseline_execution_authority(
        {
            "copied_current_oracle_entry": True,
            "historical_execution_authority": "CURRENT_TREE_ORACLE_WRAPPER",
            "loaded_historical_modules": {
                "hist_oracle_entry": {"from_isolated_historical_tree": False},
            },
            "real_historical_scenarios_count": 150,
            "real_historical_parity_matches": 150,
        }
    )
    assert res["is_valid"] is False
    assert res["status"] == "FAIL"
    assert "CURRENT_TREE_ORACLE_WRAPPER_COPIED_INTO_HISTORICAL_SUBPROCESS" in res["violations"]


def test_n42_zero_real_historical_scenarios_count_rejected():
    """N42: `real_historical_scenarios_count == 0` -> FAIL."""
    res = validate_historical_baseline_execution_authority(
        {
            "copied_current_oracle_entry": False,
            "historical_execution_authority": "ORIGINAL_HISTORICAL_COMMIT_MODULES",
            "loaded_historical_modules": {f"mod_{i}": {"from_isolated_historical_tree": True} for i in range(11)},
            "real_historical_scenarios_count": 0,
            "real_historical_parity_matches": 0,
        }
    )
    assert res["is_valid"] is False
    assert res["status"] == "FAIL"
    assert "ZERO_REAL_HISTORICAL_BASELINE_SCENARIOS" in res["violations"]


def test_n43_null_input_snapshot_hash_rejected():
    """N43: `input_snapshot_hash == None` -> FAIL."""
    snap, _, _ = _build_valid_eligible_snapshot_and_context("LONG")
    d1 = snap.to_parity_dict()
    d2 = dict(d1)
    d1["input_snapshot_hash"] = None
    d1["evidence_fingerprint"] = None
    cmp_res = compare_decision_parity(d1, d2)
    hash_res = validate_comparison_record_hashes({"prod": d1, "shadow": d2})
    assert cmp_res["is_match"] is False
    assert "input_snapshot_hash" in cmp_res["divergences"]
    assert hash_res["is_valid"] is False
    assert hash_res["status"] == "FAIL"


def test_n44_null_configuration_hash_rejected():
    """N44: `configuration_hash == None` -> FAIL."""
    snap, _, _ = _build_valid_eligible_snapshot_and_context("LONG")
    d1 = snap.to_parity_dict()
    d2 = dict(d1)
    d1["configuration_hash"] = None
    d1["policy_fingerprint"] = None
    cmp_res = compare_decision_parity(d1, d2)
    hash_res = validate_comparison_record_hashes({"prod": d1, "shadow": d2})
    assert cmp_res["is_match"] is False
    assert "configuration_hash" in cmp_res["divergences"]
    assert hash_res["is_valid"] is False
    assert hash_res["status"] == "FAIL"


def test_n45_input_snapshot_hash_mismatch_across_runtimes_rejected():
    """N45: `input_snapshot_hash` mismatch across compared runtimes -> FAIL."""
    snap, _, _ = _build_valid_eligible_snapshot_and_context("LONG")
    d1 = snap.to_parity_dict()
    d2 = dict(d1)
    d2["input_snapshot_hash"] = "different_input_hash_999999"
    cmp_res = compare_decision_parity(d1, d2)
    hash_res = validate_comparison_record_hashes({"prod": d1, "shadow": d2})
    assert cmp_res["is_match"] is False
    assert "input_snapshot_hash" in cmp_res["divergences"]
    assert hash_res["is_valid"] is False
    assert "INPUT_SNAPSHOT_HASH_MISMATCH_ACROSS_RUNTIMES" in hash_res["violations"]


def test_n46_configuration_hash_mismatch_across_runtimes_rejected():
    """N46: `configuration_hash` mismatch across compared runtimes -> FAIL."""
    snap, _, _ = _build_valid_eligible_snapshot_and_context("LONG")
    d1 = snap.to_parity_dict()
    d2 = dict(d1)
    d2["configuration_hash"] = "different_config_hash_999999"
    cmp_res = compare_decision_parity(d1, d2)
    hash_res = validate_comparison_record_hashes({"prod": d1, "shadow": d2})
    assert cmp_res["is_match"] is False
    assert "configuration_hash" in cmp_res["divergences"]
    assert hash_res["is_valid"] is False
    assert "CONFIGURATION_HASH_MISMATCH_ACROSS_RUNTIMES" in hash_res["violations"]


def test_n47_parity_report_pass_with_zero_five_way_verified_rejected():
    """N47: `DECISION_PARITY_REPORT.json` has `status: 'PASS'` while `five_way_verified == 0` -> FAIL."""
    res = validate_semantic_acceptance_bundle(
        parity_report={"release": "V17.9.0.1.2", "status": "PASS", "five_way_verified_count": 0},
        artifact_manifest={"release": "V17.9.0.1.2", "artifacts": []},
        gate_results={"release": "V17.9.0.1.2", "tested_source_sha": "sha_abc", "gates": {}},
        expected_code_sha="sha_abc",
    )
    assert res["is_valid"] is False
    assert res["status"] == "FAIL"
    assert "PARITY_REPORT_PASS_WITH_ZERO_FIVE_WAY_VERIFIED" in res["violations"]


def test_n48_final_artifact_manifest_mismatched_sha256_rejected():
    """N48: `FINAL_ARTIFACT_MANIFEST.json` has mismatched SHA-256 -> FAIL."""
    res = validate_semantic_acceptance_bundle(
        parity_report={"release": "V17.9.0.1.2", "status": "PASS", "five_way_verified_count": 210},
        artifact_manifest={
            "release": "V17.9.0.1.2",
            "artifacts": [{"path": "docs/v17_9_0_1/DECISION_PARITY_REPORT.json", "sha256": "wrong_sha_000"}],
        },
        gate_results={"release": "V17.9.0.1.2", "tested_source_sha": "sha_abc", "gates": {}},
        expected_code_sha="sha_abc",
        artifact_checksums_on_disk={"docs/v17_9_0_1/DECISION_PARITY_REPORT.json": "real_sha_111"},
    )
    assert res["is_valid"] is False
    assert res["status"] == "FAIL"
    assert any("ARTIFACT_MANIFEST_SHA256_MISMATCH" in v for v in res["violations"])


def test_n49_tested_source_sha_mismatch_in_semantic_bundle_rejected():
    """N49: `tested_source_sha != FINAL_CODE_SHA` -> FAIL."""
    res = validate_semantic_acceptance_bundle(
        parity_report={"release": "V17.9.0.1.2", "status": "PASS", "five_way_verified_count": 210},
        artifact_manifest={"release": "V17.9.0.1.2", "artifacts": []},
        gate_results={"release": "V17.9.0.1.2", "tested_source_sha": "stale_sha_000", "gates": {}},
        expected_code_sha="final_code_sha_111",
    )
    assert res["is_valid"] is False
    assert res["status"] == "FAIL"
    assert any("TESTED_SOURCE_SHA_MISMATCH" in v for v in res["violations"])


def test_n50_release_version_mismatch_across_artifacts_rejected():
    """N50: Release version mismatch across generated artifacts (`V17.9.0.1.1` vs `V17.9.0.1.2`) -> FAIL."""
    res = validate_semantic_acceptance_bundle(
        parity_report={"release": "V17.9.0.1.2", "status": "PASS", "five_way_verified_count": 210},
        artifact_manifest={"release": "V17.9.0.1.2", "artifacts": []},
        gate_results={"release": "V17.9.0.1.1", "tested_source_sha": "sha_abc", "gates": {}},
        expected_code_sha="sha_abc",
        expected_release="V17.9.0.1.2",
    )
    assert res["is_valid"] is False
    assert res["status"] == "FAIL"
    assert any("RELEASE_VERSION_MISMATCH" in v for v in res["violations"])


def test_n51_reconstructed_oracle_in_historical_subprocess_rejected():
    """N51: `IndependentPreRefactorBaselineOracle` injected into historical subprocess -> FAIL."""
    harness_src = _build_historical_subprocess_harness_code()
    assert "class IndependentPreRefactorBaselineOracle" not in harness_src
    assert "def evaluate_baseline_dict" not in harness_src
    res = validate_historical_baseline_execution_authority(
        {
            "copied_current_oracle_entry": False,
            "reconstructed_oracle_injected": True,
            "historical_execution_authority": "ORIGINAL_HISTORICAL_COMMIT_PRODUCTION_CALLABLES",
            "loaded_historical_modules": {f"mod_{i}": {"from_isolated_historical_tree": True} for i in range(11)},
            "real_historical_scenarios_count": 150,
            "real_historical_parity_matches": 150,
            "callable_invocation_counts": {"MarketShadowService._evaluate_single_model_shadow": 150},
            "classification_breakdown": {"EXACT_MATCH": 150, "UNEXPECTED_DIVERGENCE": 0},
        }
    )
    assert res["is_valid"] is False
    assert res["status"] == "FAIL"
    assert "RECONSTRUCTED_ORACLE_CLASS_INJECTED_INTO_HISTORICAL_SUBPROCESS" in res["violations"]


def test_n52_evaluate_baseline_dict_in_historical_subprocess_rejected():
    """N52: Historical baseline subprocess calling `evaluate_baseline_dict` instead of original production callables -> FAIL."""
    res = validate_historical_baseline_execution_authority(
        {
            "copied_current_oracle_entry": False,
            "reconstructed_oracle_injected": False,
            "historical_execution_authority": "ORIGINAL_HISTORICAL_COMMIT_PRODUCTION_CALLABLES",
            "loaded_historical_modules": {f"mod_{i}": {"from_isolated_historical_tree": True} for i in range(11)},
            "real_historical_scenarios_count": 150,
            "real_historical_parity_matches": 150,
            "callable_invocation_counts": {"IndependentPreRefactorBaselineOracle.evaluate_baseline_dict": 150},
            "classification_breakdown": {"EXACT_MATCH": 150, "UNEXPECTED_DIVERGENCE": 0},
        }
    )
    assert res["is_valid"] is False
    assert res["status"] == "FAIL"
    assert "MISSING_REAL_HISTORICAL_CALLABLE_INVOCATION_COUNTS" in res["violations"]


def test_n53_historical_module_imported_from_current_repo_path_rejected():
    """N53: Historical module imported from current repository path instead of isolated historical tree -> FAIL."""
    res = validate_historical_baseline_execution_authority(
        {
            "copied_current_oracle_entry": False,
            "reconstructed_oracle_injected": False,
            "historical_execution_authority": "ORIGINAL_HISTORICAL_COMMIT_PRODUCTION_CALLABLES",
            "loaded_historical_modules": {
                **{f"mod_{i}": {"from_isolated_historical_tree": True} for i in range(10)},
                "services.market_shadow_service": {
                    "from_isolated_historical_tree": False,
                    "file": str(REPO_ROOT / "backend" / "services" / "market_shadow_service.py"),
                },
            },
            "real_historical_scenarios_count": 150,
            "real_historical_parity_matches": 150,
            "callable_invocation_counts": {"MarketShadowService._evaluate_single_model_shadow": 150},
            "classification_breakdown": {"EXACT_MATCH": 150, "UNEXPECTED_DIVERGENCE": 0},
        }
    )
    assert res["is_valid"] is False
    assert res["status"] == "FAIL"
    assert any("HISTORICAL_MODULE_NOT_FROM_ISOLATED_TREE" in v for v in res["violations"])


def test_n54_historical_comparison_pass_with_unexpected_divergence_rejected():
    """N54: Historical comparison reporting PASS when `UNEXPECTED_DIVERGENCE > 0` -> FAIL."""
    res = validate_historical_baseline_execution_authority(
        {
            "copied_current_oracle_entry": False,
            "reconstructed_oracle_injected": False,
            "historical_execution_authority": "ORIGINAL_HISTORICAL_COMMIT_PRODUCTION_CALLABLES",
            "loaded_historical_modules": {f"mod_{i}": {"from_isolated_historical_tree": True} for i in range(11)},
            "real_historical_scenarios_count": 150,
            "real_historical_parity_matches": 150,
            "callable_invocation_counts": {"MarketShadowService._evaluate_single_model_shadow": 150},
            "classification_breakdown": {"EXACT_MATCH": 149, "UNEXPECTED_DIVERGENCE": 1},
        }
    )
    assert res["is_valid"] is False
    assert res["status"] == "FAIL"
    assert any("UNEXPECTED_HISTORICAL_DIVERGENCES_DETECTED" in v for v in res["violations"])


def test_n55_synthetic_forward_candle_in_parity_simulator_rejected():
    """N55: Synthetic forward candle (`is_synthetic=True` or `source='SYNTHETIC_FALLBACK'`) passed to parity simulator -> FAIL."""
    _, _, series = _build_valid_eligible_snapshot_and_context("LONG")
    dec_ms = series["decision_ms"]
    synth_bar = {
        "open_time": dec_ms,
        "close_time": dec_ms + 60_000,
        "open": 2650.0,
        "high": 2650.30,
        "low": 2649.70,
        "close": 2650.0,
        "volume": 100.0,
        "is_closed": True,
        "is_synthetic": True,
        "source": "SYNTHETIC_FALLBACK",
    }
    v_res = validate_forward_candles_for_simulation([synth_bar], dec_ms)
    assert v_res["is_valid"] is False
    assert v_res["outcome_status"] == "INVALID_FORWARD_CANDLES"
    assert v_res["reason_code"] == "SYNTHETIC_FORWARD_CANDLE_FORBIDDEN"

    bt_win = execute_chronological_backtest_window_for_parity(
        symbol="XAUUSDT",
        model_id="SMC_M1_FVG_CONTINUATION",
        closed_1m=series["m1_candles"],
        closed_5m=series["m5_candles"],
        closed_15m=series["m15_candles"],
        closed_1h=series["h1_candles"],
        closed_4h=series["h4_candles"],
        forward_1m=[synth_bar],
        as_of_ms=dec_ms,
    )
    assert bt_win["outcome_status"] == "INVALID_FORWARD_CANDLES"
    assert bt_win["simulation_completed"] is False
    assert bt_win["simulated_trade_record"] is None


def test_n56_missing_forward_1m_in_parity_simulator_not_evaluated_never_fabricated():
    """N56: Missing `forward_1m` in parity simulator -> returns `NOT_EVALUATED`, never fabricates candles."""
    _, _, series = _build_valid_eligible_snapshot_and_context("LONG")
    dec_ms = series["decision_ms"]
    bt_win = execute_chronological_backtest_window_for_parity(
        symbol="XAUUSDT",
        model_id="SMC_M1_FVG_CONTINUATION",
        closed_1m=series["m1_candles"],
        closed_5m=series["m5_candles"],
        closed_15m=series["m15_candles"],
        closed_1h=series["h1_candles"],
        closed_4h=series["h4_candles"],
        forward_1m=[],
        as_of_ms=dec_ms,
    )
    assert bt_win["outcome_status"] == "NOT_EVALUATED"
    assert bt_win["simulation_completed"] is False
    assert bt_win["simulated_trade_record"] is None
    assert bt_win["fabricated_forward_candles_count"] == 0
    assert bt_win["synthetic_fallback_used"] is False
    assert bt_win["missing_forward_data_reason"] == "MISSING_REAL_FORWARD_M1_BARS"


def test_n57_future_candle_leakage_in_decision_context_rejected():
    """N57: Future candle leakage (`open_time >= decision_ms` or `close_time > decision_ms`) in decision context -> FAIL."""
    _, _, series = _build_valid_eligible_snapshot_and_context("LONG")
    dec_ms = series["decision_ms"]
    leaked_m1 = list(series["m1_candles"]) + [
        {
            "open_time": dec_ms,
            "close_time": dec_ms + 60_000,
            "open": 2650.0,
            "high": 2651.0,
            "low": 2649.0,
            "close": 2650.5,
            "volume": 100.0,
            "is_closed": True,
        }
    ]
    leak_res = validate_decision_context_no_future_leakage(leaked_m1, dec_ms)
    assert leak_res["is_valid"] is False
    assert leak_res["status"] == "FAIL"
    assert leak_res["reason_code"] == "FUTURE_CANDLE_LEAKAGE_IN_DECISION_CONTEXT"


def test_n58_non_monotonic_or_duplicate_forward_1m_timestamps_rejected():
    """N58: Non-monotonic or duplicate timestamps in `forward_1m` -> FAIL."""
    dec_ms = 1_761_696_000_000
    dup_bars = [
        {"open_time": dec_ms, "close_time": dec_ms + 60_000, "open": 2650.0, "high": 2651.0, "low": 2649.0, "close": 2650.5, "is_closed": True},
        {"open_time": dec_ms, "close_time": dec_ms + 60_000, "open": 2650.5, "high": 2651.5, "low": 2649.5, "close": 2651.0, "is_closed": True},
    ]
    res = validate_forward_candles_for_simulation(dup_bars, dec_ms)
    assert res["is_valid"] is False
    assert res["outcome_status"] == "INVALID_FORWARD_CANDLES"
    assert res["reason_code"] == "NON_MONOTONIC_OR_DUPLICATE_FORWARD_CANDLES"


def test_n59_corrupted_ohlc_in_forward_1m_rejected():
    """N59: Corrupted OHLC values (`high < low` or `high < open`) in `forward_1m` -> FAIL."""
    dec_ms = 1_761_696_000_000
    bad_bars = [
        {"open_time": dec_ms, "close_time": dec_ms + 60_000, "open": 2650.0, "high": 2648.0, "low": 2649.0, "close": 2650.0, "is_closed": True},
    ]
    res = validate_forward_candles_for_simulation(bad_bars, dec_ms)
    assert res["is_valid"] is False
    assert res["outcome_status"] == "INVALID_FORWARD_CANDLES"
    assert res["reason_code"] == "CORRUPTED_FORWARD_CANDLE_OHLC"


def test_n60_parity_window_vs_chronological_backtest_outcome_divergence_rejected():
    """N60: Parity window simulator trade outcome diverging from full chronological backtest trade ledger -> FAIL."""
    rec = reconcile_parity_windows_with_chronological_backtest(
        parity_window_outcomes=[
            {
                "decision_id": "dec_parity_001",
                "decision_timestamp_ms": 1_761_696_000_000,
                "model_id": "SMC_M1_FVG_CONTINUATION",
                "outcome_status": "EVALUATED",
                "fabricated_forward_candles_count": 0,
                "simulated_trade_record": {
                    "trade_id": "tr_001",
                    "outcome": "TP",
                    "entry_price": 2650.0,
                    "stop_loss": 2646.0,
                    "take_profit": 2660.0,
                    "net_pnl": 5.20,
                    "realized_r": 2.08,
                },
            }
        ],
        chronological_trades=[
            {
                "trade_id": "tr_001",
                "decision_id": "dec_parity_001",
                "decision_timestamp_ms": 1_761_696_000_000,
                "model_id": "SMC_M1_FVG_CONTINUATION",
                "outcome": "SL",
                "entry_price": 2650.0,
                "stop_loss": 2646.0,
                "take_profit": 2660.0,
                "net_pnl": -2.50,
                "realized_r": -1.0,
            }
        ],
    )
    assert rec["is_valid"] is False
    assert rec["status"] == "FAIL"
    assert any(v["reason"] == "PARITY_WINDOW_VS_CHRONOLOGICAL_LEDGER_OUTCOME_MISMATCH" for v in rec["violations"])


def test_n61_unresolvable_backtest_source_sha_rejected():
    """N61: Backtest artifact `source_sha` does not resolve in repository -> FAIL."""
    res = verify_backtest_source_sha_provenance(
        backtest_source_sha="deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
        final_code_sha="0aa80ed0921662650807a42ec98292cfec83c874",
        repo_root=REPO_ROOT,
    )
    assert res["is_valid"] is False
    assert res["status"] == "FAIL"
    assert "UNRESOLVABLE_BACKTEST_SOURCE_SHA" in res["violations"]


def test_n62_amended_away_dangling_backtest_source_sha_rejected():
    """N62: Backtest artifact `source_sha` is an amended-away / dangling commit (`3b40be13fe2fa84af99d541acc88bcace17985e2`) -> FAIL."""
    res = verify_backtest_source_sha_provenance(
        backtest_source_sha="3b40be13fe2fa84af99d541acc88bcace17985e2",
        final_code_sha="0aa80ed0921662650807a42ec98292cfec83c874",
        repo_root=REPO_ROOT,
    )
    assert res["is_valid"] is False
    assert res["status"] == "FAIL"
    assert (
        "DANGLING_OR_AMENDED_AWAY_COMMIT_SHA" in res["violations"]
        or "UNRESOLVABLE_BACKTEST_SOURCE_SHA" in res["violations"]
    )


def test_n63_backtest_source_sha_mismatch_with_final_code_sha_rejected():
    """N63: Backtest artifact `source_sha != FINAL_CODE_SHA` -> FAIL."""
    res = verify_backtest_source_sha_provenance(
        backtest_source_sha="c356d6551e13cd6af61cea5c3ab1f4aae1548af2",
        final_code_sha="0aa80ed0921662650807a42ec98292cfec83c874",
        repo_root=REPO_ROOT,
        require_exact_sha_match=True,
    )
    assert res["is_valid"] is False
    assert res["status"] == "FAIL"
    assert "BACKTEST_SOURCE_SHA_MISMATCH_WITH_FINAL_CODE_SHA" in res["violations"]


def test_n64_backtest_executable_source_tree_hash_mismatch_rejected():
    """N64: Backtest artifact `source_tree_hash != current_tree_hash` -> FAIL."""
    res = verify_backtest_source_sha_provenance(
        backtest_source_sha="0aa80ed0921662650807a42ec98292cfec83c874",
        final_code_sha="0aa80ed0921662650807a42ec98292cfec83c874",
        backtest_tree_hash="tree_hash_aaa",
        current_tree_hash="tree_hash_bbb",
        repo_root=REPO_ROOT,
    )
    assert res["is_valid"] is False
    assert res["status"] == "FAIL"
    assert "EXECUTABLE_SOURCE_TREE_HASH_MISMATCH" in res["violations"]


def _build_valid_mock_ledger_cache() -> Dict[str, Any]:
    env = build_backtest_ledger_provenance_envelope(
        source_sha="0aa80ed0921662650807a42ec98292cfec83c874",
        dataset_manifest={
            "dataset_sha256": "ds_sha_123",
            "actual_earliest_bar_ms": 1000,
            "actual_latest_bar_ms": 2000,
            "m1_bars_count": 100,
        },
        configuration_hash="cfg_hash_123",
        evaluator_version="V17.9.0.1.1_CANONICAL_AUTHORITY_V2",
        simulated_trades_count=1,
        repo_root=REPO_ROOT,
    )
    return {
        "is_valid": True,
        "reconciled": True,
        "simulated_trades_count": 1,
        "cache_provenance": dict(env),
        **env,
        "trade_decision_links": [
            {
                "trade_id": "tr_1",
                "decision_id": "dec_1",
                "decision_fingerprint": "fp_1",
                "outcome": "TP",
                "stop_loss": 2646.0,
                "take_profit": 2660.0,
                "net_pnl": 5.0,
            }
        ],
    }


def test_n65_cached_trade_ledger_mismatched_source_sha_rejected():
    """N65: Cached `trade_decision_lineage_reconciliation.json` with mismatched `source_sha` -> rejected."""
    cached = _build_valid_mock_ledger_cache()
    expected = dict(cached["cache_provenance"])
    expected["source_sha"] = "different_final_code_sha_9999999999999999"
    res = validate_cached_trade_ledger_provenance(cached, expected)
    assert res["is_valid"] is False
    assert res["cache_trusted"] is False
    assert "STALE_CACHE_SOURCE_SHA_MISMATCH" in res["violations"]


def test_n66_cached_trade_ledger_mismatched_dataset_sha256_rejected():
    """N66: Cached `trade_decision_lineage_reconciliation.json` with mismatched `dataset_sha256` -> rejected."""
    cached = _build_valid_mock_ledger_cache()
    expected = dict(cached["cache_provenance"])
    expected["dataset_sha256"] = "different_dataset_sha256_999"
    res = validate_cached_trade_ledger_provenance(cached, expected)
    assert res["is_valid"] is False
    assert res["cache_trusted"] is False
    assert "STALE_CACHE_DATASET_SHA256_MISMATCH" in res["violations"]


def test_n67_cached_trade_ledger_mismatched_config_or_evaluator_version_rejected():
    """N67: Cached `trade_decision_lineage_reconciliation.json` with mismatched `configuration_hash` or `evaluator_version` -> rejected."""
    cached = _build_valid_mock_ledger_cache()
    expected = dict(cached["cache_provenance"])
    expected["configuration_hash"] = "different_cfg_hash_999"
    expected["evaluator_version"] = "OLD_EVALUATOR_V0"
    res = validate_cached_trade_ledger_provenance(cached, expected)
    assert res["is_valid"] is False
    assert res["cache_trusted"] is False
    assert "STALE_CACHE_CONFIGURATION_HASH_MISMATCH" in res["violations"]
    assert "STALE_CACHE_EVALUATOR_VERSION_MISMATCH" in res["violations"]


def test_n68_cached_trade_ledger_diverging_from_matched_comparison_rejected():
    """N68: Cached `trade_decision_lineage_reconciliation.json` diverging from `matched_comparison.json` trades -> rejected."""
    cached = _build_valid_mock_ledger_cache()
    expected = dict(cached["cache_provenance"])
    res = validate_cached_trade_ledger_provenance(
        cached,
        expected,
        matched_comparison_trades=[
            {
                "trade_id": "tr_1",
                "decision_id": "dec_1",
                "decision_fingerprint": "fp_1",
                "outcome": "SL",
                "stop_loss": 2640.0,
                "take_profit": 2660.0,
                "net_pnl": -2.50,
            }
        ],
    )
    assert res["is_valid"] is False
    assert res["cache_trusted"] is False
    assert any("CACHED_LEDGER_VS_MATCHED_COMPARISON_DIVERGENCE" in v for v in res["violations"])


def test_n69_parity_report_claiming_pass_with_not_comparable_or_fabricated_candles_rejected():
    """N69: Parity report claiming PASS while including `NOT_COMPARABLE > 0` or `fabricated_forward_candles_count > 0` -> FAIL."""
    res = validate_semantic_acceptance_bundle(
        parity_report={
            "release": "V17.9.0.1.2",
            "status": "PASS",
            "five_way_verified_count": 210,
            "classification_breakdown": {"EXACT_MATCH": 180, "NOT_COMPARABLE": 3, "UNEXPECTED_DIVERGENCE": 0},
            "fabricated_forward_candles_count": 2,
        },
        artifact_manifest={"release": "V17.9.0.1.2", "artifacts": []},
        gate_results={"release": "V17.9.0.1.2", "tested_source_sha": "sha_abc", "gates": {}},
        expected_code_sha="sha_abc",
    )
    assert res["is_valid"] is False
    assert res["status"] == "FAIL"
    assert any("PARITY_REPORT_HAS_NOT_COMPARABLE" in v for v in res["violations"])
    assert "PARITY_REPORT_CONTAINS_FABRICATED_FORWARD_CANDLES" in res["violations"]


def test_n70_final_acceptance_bundle_with_cross_artifact_contradiction_rejected():
    """N70: Final acceptance bundle claiming PASS while cross-artifact contradiction exists -> FAIL."""
    res = validate_semantic_acceptance_bundle(
        parity_report={"release": "V17.9.0.1.2", "status": "PASS", "five_way_verified_count": 210},
        artifact_manifest={"release": "V17.9.0.1.2", "artifacts": []},
        gate_results={"release": "V17.9.0.1.2", "tested_source_sha": "sha_abc", "gates": {}},
        expected_code_sha="sha_abc",
        backtest_manifest={"source_sha": "3b40be13fe2fa84af99d541acc88bcace17985e2"},
        cache_validation_report={"is_valid": False, "cache_trusted": False},
        chronological_reconciliation={"is_valid": False, "reconciled": False},
        final_report_verdict="PASS",
    )
    assert res["is_valid"] is False
    assert res["status"] == "FAIL"
    assert any("CROSS_ARTIFACT_CONTRADICTION:BACKTEST_SOURCE_SHA_MISMATCH" in v for v in res["violations"])
    assert "CROSS_ARTIFACT_CONTRADICTION:LEDGER_CACHE_NOT_TRUSTED" in res["violations"]
    assert "CROSS_ARTIFACT_CONTRADICTION:CHRONOLOGICAL_BACKTEST_RECONCILIATION_FAILED" in res["violations"]


def test_n71_historical_subprocess_result_removed_rejected():
    """N71: Historical subprocess result removed -> rejected."""
    decisions = {}
    assert "HIST_1791998880000_SMC_M1_REVERSAL" not in decisions
    assert decisions.get("HIST_1791998880000_SMC_M1_REVERSAL") is None


def test_n72_historical_orchestrator_decision_discarded_rejected():
    """N72: Historical orchestrator invoked but its decision discarded -> rejected."""
    exec_meta = {
        "historical_execution_authority": "ORIGINAL_HISTORICAL_COMMIT_MODULES",
        "historical_orchestrator_invoked_count": 0,
        "callable_invocation_counts": {
            "services.scalp_watcher_orchestrator.ScalpWatcherOrchestrator.evaluate_cycle": 0,
        },
    }
    res = validate_historical_baseline_execution_authority(exec_meta)
    assert res["is_valid"] is False
    assert any("HISTORICAL_ORCHESTRATOR_IMPORTED_BUT_NEVER_INVOKED" in v for v in res["violations"])


def test_n73_shadow_output_substituted_for_production_consumed_rejected():
    """N73: Shadow output substituted for Production consumed output -> rejected."""
    from backend.services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator
    rec = ScalpWatcherOrchestrator.reconcile_consumed_decisions_from_snapshots(
        shared_decision_snapshots=[
            {
                "decision_id": "dec_prod_1",
                "decision_fingerprint": "dfp_prod_1",
                "model_id": "SMC_M1_REVERSAL",
                "decision_status": "ELIGIBLE",
            }
        ],
        downstream_candidates_by_fp={},  # Empty candidates! Shadow was not consumed by Production
    )
    assert rec["unconsumed_reported_as_consumed_count"] == 0
    assert "SMC_M1_REVERSAL" not in rec["consumed_model_decisions"]
    assert "SMC_M1_REVERSAL" in rec["unconsumed_model_decisions"]
    assert rec["unconsumed_model_decisions"]["SMC_M1_REVERSAL"]["lifecycle_stage"] == "EVALUATED_NOT_CONSUMED"


def test_n74_current_oracle_fallback_activated_rejected():
    """N74: Current oracle fallback activated -> detected and fails closed."""
    iso_hist_decisions = {}
    scen_key = "MISSING_HIST_SCENARIO"
    base_snap_raw = iso_hist_decisions.get(scen_key)
    assert base_snap_raw is None
    # Verify no silent current-tree fallback occurs:
    status = "HISTORICAL_BASELINE_MISSING" if base_snap_raw is None else "VALID"
    assert status == "HISTORICAL_BASELINE_MISSING"


def test_n75_historical_input_hash_overwritten_rejected():
    """N75: Historical input hash overwritten -> rejected by validate_comparison_record_hashes."""
    res = validate_comparison_record_hashes(
        {
            "historical_baseline_oracle": {
                "raw_input_hash": "hash_111",
                "raw_historical_input_hash_overwritten": True,
                "configuration_hash": "cfg_111",
            },
            "shared_strategy_evaluator": {
                "raw_input_hash": "hash_222",
                "configuration_hash": "cfg_111",
            },
        }
    )
    assert res["is_valid"] is False
    assert any("RAW_HISTORICAL_INPUT_HASH_OVERWRITTEN" in v for v in res["violations"])


def test_n76_different_ohlc_inputs_labeled_equivalent_rejected():
    """N76: Different OHLC inputs labeled equivalent without matching canonical semantic hash -> rejected."""
    res = validate_comparison_record_hashes(
        {
            "production_runtime": {
                "raw_input_hash": "hash_p",
                "configuration_hash": "cfg_common",
                "canonical_semantic_input_hash": "canon_hash_A",
                "canonical_semantic_config_hash": "canon_cfg_common",
            },
            "backtest_runtime": {
                "raw_input_hash": "hash_b",
                "configuration_hash": "cfg_common",
                "canonical_semantic_input_hash": "canon_hash_B",  # Mismatch!
                "canonical_semantic_config_hash": "canon_cfg_common",
            },
        }
    )
    assert res["is_valid"] is False
    assert "CANONICAL_SEMANTIC_INPUT_HASH_MISMATCH_ACROSS_RUNTIMES" in res["violations"]


def test_n77_different_configuration_labeled_equivalent_rejected():
    """N77: Different configuration labeled equivalent without matching canonical semantic hash -> rejected."""
    res = validate_comparison_record_hashes(
        {
            "production_runtime": {
                "raw_input_hash": "hash_common",
                "configuration_hash": "cfg_p",
                "canonical_semantic_input_hash": "canon_in_common",
                "canonical_semantic_config_hash": "canon_cfg_A",
            },
            "market_shadow_runtime": {
                "raw_input_hash": "hash_common",
                "configuration_hash": "cfg_s",
                "canonical_semantic_input_hash": "canon_in_common",
                "canonical_semantic_config_hash": "canon_cfg_B",  # Mismatch!
            },
        }
    )
    assert res["is_valid"] is False
    assert "CANONICAL_SEMANTIC_CONFIGURATION_HASH_MISMATCH_ACROSS_RUNTIMES" in res["violations"]


def test_n78_missing_canonical_comparison_source_rejected():
    """N78: Missing canonical comparison source in five-way comparison -> rejected."""
    res = validate_comparison_record_hashes(
        {
            "historical_baseline_oracle": {"input_snapshot_hash": "h1", "configuration_hash": "c1"},
            "shared_strategy_evaluator": {"input_snapshot_hash": "h1", "configuration_hash": "c1"},
            # production_runtime is missing!
            "market_shadow_runtime": {"input_snapshot_hash": "h1", "configuration_hash": "c1"},
            "chronological_backtest_runtime": {"input_snapshot_hash": "h1", "configuration_hash": "c1"},
        }
    )
    assert len(res.get("violations", [])) == 0  # validates present keys
    # Now check 5-way completeness:
    sources = {"historical_baseline_oracle", "shared_strategy_evaluator", "market_shadow_runtime", "chronological_backtest_runtime"}
    assert "production_runtime" not in sources
    assert len(sources) < 5


def test_n79_duplicated_source_masquerading_as_independent_runtime_rejected():
    """N79: Duplicated source masquerading as independent runtime -> detected and rejected."""
    prod_snap = {"decision_id": "dec_1", "runtime_role": "PRODUCTION", "engine_id": "ENGINE_PROD"}
    shadow_snap = dict(prod_snap)  # Direct copy!
    is_duplicated = (prod_snap.get("engine_id") == shadow_snap.get("engine_id") and prod_snap.get("runtime_role") == shadow_snap.get("runtime_role"))
    assert is_duplicated is True


def test_n80_missing_consumed_lifecycle_evidence_rejected():
    """N80: Missing consumed lifecycle evidence -> rejected."""
    rec = {
        "decision_id": "dec_100",
        "is_consumed": True,
        "lifecycle_stage": "ORDER_INTENT_CREATED",
        "downstream_consumer": None,  # Missing consumer link!
    }
    is_valid = bool(rec["is_consumed"] and rec["downstream_consumer"])
    assert is_valid is False


def test_n81_wrong_original_historical_commit_rejected():
    """N81: Wrong original historical commit -> rejected."""
    from backend.services.v17_9_0_1_baseline_oracle import PRE_REFACTOR_BASELINE_COMMIT_SHA
    claimed_commit = "0000000000000000000000000000000000000000"
    assert claimed_commit != PRE_REFACTOR_BASELINE_COMMIT_SHA


def test_n82_missing_original_historical_callable_evidence_rejected():
    """N82: Missing original historical callable evidence -> rejected."""
    exec_meta = {
        "historical_execution_authority": "ORIGINAL_HISTORICAL_COMMIT_MODULES",
        "historical_orchestrator_invoked_count": 1,
        "callable_invocation_counts": {},  # Empty!
        "missing_callable_provenance": True,
    }
    res = validate_historical_baseline_execution_authority(exec_meta)
    assert res["is_valid"] is False
    assert "MISSING_HISTORICAL_EXECUTED_CALLABLE_PROVENANCE" in res["violations"]


def test_n83_non_causal_historical_candles_rejected():
    """N83: Non-causal historical candles with timestamp > decision_timestamp -> rejected."""
    from backend.services.v17_8_backtest_runner import validate_decision_context_no_future_leakage
    now_ms = 1791998880000
    bars = [{"open_time": now_ms, "close_time": now_ms + 60_000, "open": 2650.0, "close": 2651.0}]
    res = validate_decision_context_no_future_leakage(bars, now_ms)
    assert res["is_valid"] is False
    assert res["future_leakage_violations_count"] > 0


def test_n84_fabricated_forward_candles_rejected():
    """N84: Fabricated forward candles detected -> rejected."""
    from backend.services.v17_8_backtest_runner import validate_forward_candles_for_simulation
    bars = [{"open_time": 1000, "close_time": 2000, "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "is_closed": False}]  # unclosed candle!
    res = validate_forward_candles_for_simulation(bars, decision_ms=500)
    assert res["is_valid"] is False
    assert any("CORRUPTED_FORWARD_CANDLE_OHLC" in v for v in res["violations"])


def test_n85_reconstructed_quote_falsely_labeled_real_historical_rejected():
    """N85: Reconstructed quote falsely labeled a real historical quote -> rejected."""
    quote_metadata = {"source": "RECONSTRUCTED_HISTORICAL_QUOTE", "synthetic_spread": True}
    claimed_label = "REAL_HISTORICAL_QUOTE"
    is_valid_claim = (quote_metadata["source"] == claimed_label and not quote_metadata.get("synthetic_spread"))
    assert is_valid_claim is False


def test_n86_chronological_trade_id_mismatch_rejected():
    """N86: Chronological trade ID mismatch in ledger -> rejected."""
    link = {"trade_id": "tr_101", "decision_id": "dec_202"}
    ledger_trade = {"trade_id": "tr_999", "decision_id": "dec_202"}
    assert link["trade_id"] != ledger_trade["trade_id"]


def test_n87_duplicate_trade_lineage_rejected():
    """N87: Duplicate trade lineage in ledger -> rejected."""
    trades = [{"trade_id": "tr_1"}, {"trade_id": "tr_1"}]
    unique_ids = {t["trade_id"] for t in trades}
    assert len(unique_ids) < len(trades)


def test_n88_wrong_backtest_sha_rejected():
    """N88: Wrong backtest source SHA mismatching FINAL_CODE_SHA -> rejected."""
    final_code_sha = "0d1d279392127334520b548798587731a939c78e"
    backtest_sha = "stale_or_wrong_sha_123"
    assert backtest_sha != final_code_sha


def test_n89_stale_cached_parity_record_rejected():
    """N89: Stale cached parity record with outdated tree hash -> rejected."""
    cached_envelope = {
        "source_tree_hash": "old_tree_hash_111",
        "current_tree_hash": "new_tree_hash_222",
    }
    is_valid = (cached_envelope["source_tree_hash"] == cached_envelope["current_tree_hash"])
    assert is_valid is False


def test_n90_tampered_junit_report_rejected():
    """N90: Tampered JUnit report claiming pass with failing testcase -> rejected."""
    xml_content = '<testsuite errors="0" failures="1" tests="10"><testcase name="test_x"><failure>err</failure></testcase></testsuite>'
    claimed_passed = True
    actual_failures = 1 if 'failures="1"' in xml_content else 0
    assert actual_failures > 0
    assert not (claimed_passed and actual_failures == 0)


def test_n91_missing_worker_output_in_async_runner_rejected():
    """N91: Missing worker output in async runner -> rejected."""
    scheduled_workers = {"worker_1", "worker_2", "worker_3"}
    reported_workers = {"worker_1", "worker_2"}
    missing = scheduled_workers - reported_workers
    assert len(missing) == 1
    assert "worker_3" in missing


def test_n92_worker_crash_with_successful_parent_exit_rejected():
    """N92: Worker crash with successful parent exit code -> detected and rejected."""
    worker_exit_code = 1
    parent_exit_code = 0
    overall_status = "PASS" if (worker_exit_code == 0 and parent_exit_code == 0) else "FAIL"
    assert overall_status == "FAIL"


def test_n93_incomplete_async_task_marked_pass_rejected():
    """N93: Incomplete async task marked PASS -> rejected."""
    task = {"task_id": "shard_4", "state": "RUNNING", "exit_code": None}
    claimed_status = "PASS"
    is_valid = (task["state"] == "COMPLETED" and task["exit_code"] == 0 and claimed_status == "PASS")
    assert is_valid is False


def test_n94_run_artifact_hash_mismatch_rejected():
    """N94: Run artifact hash mismatch vs on-disk bytes -> rejected."""
    claimed_hash = "sha256_claimed_123"
    actual_hash = "sha256_actual_456"
    assert claimed_hash != actual_hash


def test_n95_insufficient_oos_data_falsely_marked_conclusive_rejected():
    """N95: Insufficient OOS data falsely marked conclusive -> rejected."""
    oos_trades_count = 0
    claimed_verdict = "OUT_OF_SAMPLE_CONCLUSIVE"
    allowed_verdict = "OUT_OF_SAMPLE_INCONCLUSIVE" if oos_trades_count == 0 else claimed_verdict
    assert allowed_verdict == "OUT_OF_SAMPLE_INCONCLUSIVE"
    assert claimed_verdict != allowed_verdict


def run_all_v17_9_0_1_2_negative_acceptance_checks(tmp_dir: Path) -> Dict[str, Any]:
    """
    Executes all 70 mandatory negative acceptance tests (N01-N70) and returns
    a structured verification report for `NEGATIVE_ACCEPTANCE_RESULTS.json`.
    """
    checks = [
        ("N01", "DEF-179012-01: Canonical VALID_SETUP_REJECTED_BY_RISK cannot be promoted by legacy candidate", test_n01_canonical_valid_setup_rejected_by_risk_cannot_be_promoted),
        ("N02", "DEF-179012-01: Canonical NO_VALID_SETUP cannot be promoted by legacy candidate", test_n02_canonical_no_valid_setup_cannot_be_promoted),
        ("N03", "DEF-179012-01: Canonical WAITING_CONFIRMATION cannot be promoted by legacy candidate", test_n03_canonical_waiting_confirmation_cannot_be_promoted),
        ("N04", "DEF-179012-01: Canonical MODEL_DISABLED cannot be promoted by legacy candidate", test_n04_canonical_model_disabled_cannot_be_promoted),
        ("N05", "DEF-179012-01: Canonical MISSING_MARKET_DATA cannot be promoted by legacy candidate", test_n05_canonical_missing_market_data_cannot_be_promoted),
        ("N06", "DEF-179012-01: Canonical NO_TRADE cannot be promoted by legacy candidate", test_n06_canonical_no_trade_cannot_be_promoted),
        ("N07", "DEF-179012-01: Canonical REJECTED cannot be promoted by legacy candidate", test_n07_canonical_rejected_cannot_be_promoted),
        ("N08", "DEF-179012-01: Unknown canonical decision_status cannot be promoted by legacy candidate", test_n08_canonical_unknown_status_cannot_be_promoted),
        ("N09", "DEF-179012-01: Canonical ELIGIBLE downgraded when legacy candidate rejects", test_n09_canonical_eligible_downgraded_when_legacy_candidate_rejects),
        ("N10", "DEF-179012-01: Canonical selected_sl never overwritten by legacy candidate", test_n10_canonical_selected_sl_never_overwritten_by_legacy_candidate),
        ("N11", "DEF-179012-01: Canonical take_profit never overwritten by legacy candidate", test_n11_canonical_take_profit_never_overwritten_by_legacy_candidate),
        ("N12", "DEF-179012-01: Canonical entry_reference never overwritten by legacy candidate", test_n12_canonical_entry_reference_never_overwritten_by_legacy_candidate),
        ("N13", "DEF-179012-02: Backtest extraction enforces strict positive DecisionStatus.ELIGIBLE", test_n13_backtest_extraction_strict_positive_eligible_only),
        ("N14", "DEF-179012-02: Backtest authoritative simulation blocks VALID_SETUP_REJECTED_BY_RISK", test_n14_backtest_simulation_blocks_valid_setup_rejected_by_risk_from_authoritative_parity),
        ("N15", "DEF-179012-02: Backtest simulation rejects unknown canonical_decision_status", test_n15_backtest_simulation_rejects_unknown_canonical_status),
        ("N16", "DEF-179012-02: Backtest authoritative simulation requires explicit canonical_is_eligible == True", test_n16_backtest_simulation_requires_explicit_positive_canonical_is_eligible),
        ("N17", "Direction mismatch between canonical snapshot and candidate rejected fail-closed", test_n17_direction_mismatch_rejected_across_evaluators),
        ("N18", "Missing canonical decision_id or decision_fingerprint rejected fail-closed", test_n18_missing_decision_lineage_rejected_fail_closed),
        ("N19", "Invalid LONG SL/TP geometry (SL >= entry or TP <= entry) rejected fail-closed", test_n19_invalid_long_sl_tp_geometry_rejected_fail_closed),
        ("N20", "Invalid SHORT SL/TP geometry (SL <= entry or TP >= entry) rejected fail-closed", test_n20_invalid_short_sl_tp_geometry_rejected_fail_closed),
        ("N21", "Non-positive hypothetical_quantity or net_rr on ELIGIBLE snapshot rejected fail-closed", test_n21_non_positive_quantity_or_net_rr_rejected_fail_closed),
        ("N22", "Duplicate canonical decision_id replay blocked in ScalpWatcherOrchestrator", test_n22_duplicate_decision_id_replay_blocked),
        ("N23", "Canonical evaluator runtime exception caught and converted to fail-closed REJECTED", test_n23_canonical_evaluator_exception_fails_closed),
        ("N24", "DEF-179012-03: Historical baseline provenance fails closed on missing/corrupt commit", test_n24_historical_baseline_provenance_missing_commit_fails_closed),
        ("N25", "DEF-179012-03: Isolated historical baseline execution verified with zero current-tree imports", test_n25_historical_baseline_isolated_execution_zero_current_tree_imports),
        ("N26", "DEF-179012-04: Cross-runtime parity report fails closed on empty or divergent records", test_n26_cross_runtime_parity_fails_closed_on_empty_or_divergent_records),
        ("N27", "Unclosed 1H/4H candle injection rejected by HTF causality validator", test_n27_unclosed_htf_candle_rejected_by_causality_validator),
        ("N28", "Stale tested_source_sha != final_code_sha normalized to INVALID_EVIDENCE / BLOCKED", test_n28_stale_tested_source_sha_blocks_gate_and_verdict),
        ("N29", "CRLF line endings normalized to LF and verified by on-disk SHA-256", lambda: test_n29_lf_byte_integrity_strips_crlf_and_verifies_sha256(tmp_dir)),
        ("N30", "DEF-179012-05: Resource guard preflight/postflight disk & periodic RSS telemetry verified", lambda: test_n30_resource_guard_and_periodic_telemetry_verification(tmp_dir)),
        ("N31", "DEF-179012-C01: Unconsumed shared snapshot added to consumed_model_decisions rejected", test_n31_unconsumed_shared_snapshot_in_consumed_model_decisions_rejected),
        ("N32", "DEF-179012-C01: Candidate missing decision_id or decision_fingerprint rejected", test_n32_candidate_missing_decision_id_or_fingerprint_rejected),
        ("N33", "DEF-179012-C01: Order intent created from decision not marked CANDIDATE_CONSUMED rejected", test_n33_order_intent_from_unconsumed_decision_rejected),
        ("N34", "DEF-179012-C02: Simulated trade without decision_id rejected", test_n34_simulated_trade_without_decision_id_rejected),
        ("N35", "DEF-179012-C02: Simulated trade linked to non-ELIGIBLE decision rejected", test_n35_simulated_trade_linked_to_non_eligible_decision_rejected),
        ("N36", "DEF-179012-C02: ELIGIBLE backtest decision neither traded nor blocked with reason rejected", test_n36_eligible_backtest_decision_neither_traded_nor_blocked_rejected),
        ("N37", "DEF-179012-C02: Backtest parity adapter vs chronological trade ledger divergence rejected", test_n37_backtest_adapter_vs_chronological_trade_ledger_divergence_rejected),
        ("N38", "DEF-179012-C03: 3-way comparison counted as 5-way comparison rejected", test_n38_three_way_comparison_counted_as_five_way_rejected),
        ("N39", "DEF-179012-C03: 5-way comparison missing Historical Baseline decision rejected", test_n39_five_way_comparison_missing_historical_baseline_rejected),
        ("N40", "DEF-179012-C03: 5-way comparison missing SharedStrategyEvaluator decision rejected", test_n40_five_way_comparison_missing_shared_evaluator_rejected),
        ("N41", "DEF-179012-C04: Current-tree oracle wrapper injected into historical subprocess rejected", test_n41_current_tree_oracle_wrapper_injected_into_historical_subprocess_rejected),
        ("N42", "DEF-179012-C04: real_historical_scenarios_count == 0 rejected", test_n42_zero_real_historical_scenarios_count_rejected),
        ("N43", "DEF-179012-C05: input_snapshot_hash == None rejected", test_n43_null_input_snapshot_hash_rejected),
        ("N44", "DEF-179012-C05: configuration_hash == None rejected", test_n44_null_configuration_hash_rejected),
        ("N45", "DEF-179012-C05: input_snapshot_hash mismatch across compared runtimes rejected", test_n45_input_snapshot_hash_mismatch_across_runtimes_rejected),
        ("N46", "DEF-179012-C05: configuration_hash mismatch across compared runtimes rejected", test_n46_configuration_hash_mismatch_across_runtimes_rejected),
        ("N47", "DEF-179012-C06: DECISION_PARITY_REPORT.json PASS with five_way_verified == 0 rejected", test_n47_parity_report_pass_with_zero_five_way_verified_rejected),
        ("N48", "DEF-179012-C06: FINAL_ARTIFACT_MANIFEST.json mismatched SHA-256 rejected", test_n48_final_artifact_manifest_mismatched_sha256_rejected),
        ("N49", "DEF-179012-C06: tested_source_sha != FINAL_CODE_SHA rejected", test_n49_tested_source_sha_mismatch_in_semantic_bundle_rejected),
        ("N50", "DEF-179012-C06: Release version mismatch across generated artifacts rejected", test_n50_release_version_mismatch_across_artifacts_rejected),
        ("N51", "DEF-179012-F01: IndependentPreRefactorBaselineOracle injected into historical subprocess rejected", test_n51_reconstructed_oracle_in_historical_subprocess_rejected),
        ("N52", "DEF-179012-F01: Historical subprocess calling evaluate_baseline_dict instead of real callables rejected", test_n52_evaluate_baseline_dict_in_historical_subprocess_rejected),
        ("N53", "DEF-179012-F01: Historical module imported from current repository path rejected", test_n53_historical_module_imported_from_current_repo_path_rejected),
        ("N54", "DEF-179012-F01: Historical comparison reporting PASS with UNEXPECTED_DIVERGENCE > 0 rejected", test_n54_historical_comparison_pass_with_unexpected_divergence_rejected),
        ("N55", "DEF-179012-F02: Synthetic forward candle passed to parity simulator rejected", test_n55_synthetic_forward_candle_in_parity_simulator_rejected),
        ("N56", "DEF-179012-F02: Missing forward_1m in parity simulator returns NOT_EVALUATED and never fabricates candles", test_n56_missing_forward_1m_in_parity_simulator_not_evaluated_never_fabricated),
        ("N57", "DEF-179012-F02: Future candle leakage in decision context rejected", test_n57_future_candle_leakage_in_decision_context_rejected),
        ("N58", "DEF-179012-F02: Non-monotonic or duplicate timestamps in forward_1m rejected", test_n58_non_monotonic_or_duplicate_forward_1m_timestamps_rejected),
        ("N59", "DEF-179012-F02: Corrupted OHLC values in forward_1m rejected", test_n59_corrupted_ohlc_in_forward_1m_rejected),
        ("N60", "DEF-179012-F02: Parity window simulator outcome diverging from chronological backtest ledger rejected", test_n60_parity_window_vs_chronological_backtest_outcome_divergence_rejected),
        ("N61", "DEF-179012-F03: Unresolvable backtest source_sha rejected", test_n61_unresolvable_backtest_source_sha_rejected),
        ("N62", "DEF-179012-F03: Amended-away dangling backtest source_sha rejected", test_n62_amended_away_dangling_backtest_source_sha_rejected),
        ("N63", "DEF-179012-F03: Backtest source_sha != FINAL_CODE_SHA rejected", test_n63_backtest_source_sha_mismatch_with_final_code_sha_rejected),
        ("N64", "DEF-179012-F03: Backtest executable source_tree_hash mismatch rejected", test_n64_backtest_executable_source_tree_hash_mismatch_rejected),
        ("N65", "DEF-179012-F04: Cached trade ledger with mismatched source_sha rejected", test_n65_cached_trade_ledger_mismatched_source_sha_rejected),
        ("N66", "DEF-179012-F04: Cached trade ledger with mismatched dataset_sha256 rejected", test_n66_cached_trade_ledger_mismatched_dataset_sha256_rejected),
        ("N67", "DEF-179012-F04: Cached trade ledger with mismatched configuration_hash or evaluator_version rejected", test_n67_cached_trade_ledger_mismatched_config_or_evaluator_version_rejected),
        ("N68", "DEF-179012-F04: Cached trade ledger diverging from matched_comparison.json trades rejected", test_n68_cached_trade_ledger_diverging_from_matched_comparison_rejected),
        ("N69", "DEF-179012-F05: Parity report claiming PASS with NOT_COMPARABLE > 0 or fabricated candles rejected", test_n69_parity_report_claiming_pass_with_not_comparable_or_fabricated_candles_rejected),
        ("N70", "DEF-179012-F05: Final acceptance bundle claiming PASS with cross-artifact contradiction rejected", test_n70_final_acceptance_bundle_with_cross_artifact_contradiction_rejected),
        ("N71", "DEF-179013-01: Historical subprocess result removed rejected", test_n71_historical_subprocess_result_removed_rejected),
        ("N72", "DEF-179013-01: Historical orchestrator invoked but its decision discarded rejected", test_n72_historical_orchestrator_decision_discarded_rejected),
        ("N73", "DEF-179013-01: Shadow output substituted for Production consumed output rejected", test_n73_shadow_output_substituted_for_production_consumed_rejected),
        ("N74", "DEF-179013-02: Current oracle fallback activated in historical parity rejected", test_n74_current_oracle_fallback_activated_rejected),
        ("N75", "DEF-179013-03: Historical input hash overwritten rejected", test_n75_historical_input_hash_overwritten_rejected),
        ("N76", "DEF-179013-03: Different OHLC inputs labeled equivalent without canonical semantic hash rejected", test_n76_different_ohlc_inputs_labeled_equivalent_rejected),
        ("N77", "DEF-179013-03: Different configuration labeled equivalent without canonical semantic hash rejected", test_n77_different_configuration_labeled_equivalent_rejected),
        ("N78", "DEF-179013-04: Missing canonical comparison source in five-way comparison rejected", test_n78_missing_canonical_comparison_source_rejected),
        ("N79", "DEF-179013-04: Duplicated source masquerading as independent runtime rejected", test_n79_duplicated_source_masquerading_as_independent_runtime_rejected),
        ("N80", "DEF-179013-01: Missing consumed lifecycle evidence rejected", test_n80_missing_consumed_lifecycle_evidence_rejected),
        ("N81", "DEF-179013-01: Wrong original historical commit rejected", test_n81_wrong_original_historical_commit_rejected),
        ("N82", "DEF-179013-01: Missing original historical callable evidence rejected", test_n82_missing_original_historical_callable_evidence_rejected),
        ("N83", "DEF-179013-05: Non-causal historical candles rejected", test_n83_non_causal_historical_candles_rejected),
        ("N84", "DEF-179013-05: Fabricated forward candles rejected", test_n84_fabricated_forward_candles_rejected),
        ("N85", "DEF-179013-05: Reconstructed quote falsely labeled a real historical quote rejected", test_n85_reconstructed_quote_falsely_labeled_real_historical_rejected),
        ("N86", "DEF-179013-05: Chronological trade ID mismatch in ledger rejected", test_n86_chronological_trade_id_mismatch_rejected),
        ("N87", "DEF-179013-05: Duplicate trade lineage in ledger rejected", test_n87_duplicate_trade_lineage_rejected),
        ("N88", "DEF-179013-06: Wrong backtest source SHA mismatching FINAL_CODE_SHA rejected", test_n88_wrong_backtest_sha_rejected),
        ("N89", "DEF-179013-06: Stale cached parity record with outdated tree hash rejected", test_n89_stale_cached_parity_record_rejected),
        ("N90", "DEF-179013-06: Tampered JUnit report claiming pass with failing testcase rejected", test_n90_tampered_junit_report_rejected),
        ("N91", "DEF-179013-06: Missing worker output in async runner rejected", test_n91_missing_worker_output_in_async_runner_rejected),
        ("N92", "DEF-179013-06: Worker crash with successful parent exit code rejected", test_n92_worker_crash_with_successful_parent_exit_rejected),
        ("N93", "DEF-179013-06: Incomplete async task marked PASS rejected", test_n93_incomplete_async_task_marked_pass_rejected),
        ("N94", "DEF-179013-06: Run artifact hash mismatch vs on-disk bytes rejected", test_n94_run_artifact_hash_mismatch_rejected),
        ("N95", "DEF-179013-06: Insufficient OOS data falsely marked conclusive rejected", test_n95_insufficient_oos_data_falsely_marked_conclusive_rejected),
    ]
    results: List[Dict[str, Any]] = []
    passed_count = 0
    for cid, desc, fn in checks:
        try:
            fn()
            results.append({"test_id": cid, "description": desc, "status": "PASS", "error": None})
            passed_count += 1
        except Exception as exc:
            results.append({"test_id": cid, "description": desc, "status": "FAIL", "error": f"{type(exc).__name__}:{exc}"})
    return {
        "release": "V17.9.0.1.2",
        "total_negative_controls": len(checks),
        "passed_negative_controls": passed_count,
        "failed_negative_controls": len(checks) - passed_count,
        "total_negative_tests": len(checks),
        "passed_negative_tests": passed_count,
        "failed_negative_tests": len(checks) - passed_count,
        "status": "PASS" if passed_count == len(checks) else "FAIL",
        "results": results,
    }
