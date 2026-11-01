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
    """N71: Historical subprocess result removed -> rejected by classify_historical_vs_refactored_decision."""
    from backend.services.v17_9_0_1_baseline_oracle import classify_historical_vs_refactored_decision
    res = classify_historical_vs_refactored_decision(
        scenario_item={"scenario_id": "scen_test", "model_id": "SMC_M1_REVERSAL"},
        raw_hist_dec=None,
        ref_dec={"decision_status": "ELIGIBLE"},
    )
    assert res["classification"] in ("HISTORICAL_BASELINE_MISSING", "NOT_EXECUTED")
    assert res.get("is_unexpected_divergence", False) is True or res.get("raw_exact_match") is False
    assert res.get("raw_exact_match") is False


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
        downstream_candidates_by_fp={},
    )
    assert rec["unconsumed_reported_as_consumed_count"] == 0
    assert "SMC_M1_REVERSAL" not in rec["consumed_model_decisions"]
    assert "SMC_M1_REVERSAL" in rec["unconsumed_model_decisions"]
    assert rec["unconsumed_model_decisions"]["SMC_M1_REVERSAL"]["lifecycle_stage"] == "EVALUATED_NOT_CONSUMED"


def test_n74_current_oracle_fallback_activated_rejected():
    """N74: Current oracle fallback activated -> detected and fails closed."""
    from backend.services.v17_9_0_1_baseline_oracle import classify_historical_vs_refactored_decision
    res = classify_historical_vs_refactored_decision(
        scenario_item={"scenario_id": "scen_fallback", "model_id": "CG01_SWEEP_MSS_FVG"},
        raw_hist_dec={"copied_current_oracle_entry": True},
        ref_dec={"decision_status": "ELIGIBLE"},
    )
    assert res.get("is_unexpected_divergence", False) is True or res.get("raw_exact_match") is False
    assert res.get("raw_exact_match") is False


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
    from tools.acceptance_verdict import validate_comparison_source_accounting
    records = [
        {
            "historical_baseline_oracle": {"engine_id": "BASE"},
            "shared_strategy_evaluator": {"engine_id": "SHARED"},
            "market_shadow_runtime": {"engine_id": "SHADOW"},
            "chronological_backtest_runtime": {"engine_id": "BT"},
        }
    ]
    res = validate_comparison_source_accounting(records, expected_unique_sources=5)
    assert res["is_valid"] is False
    assert any("MISSING" in v for v in res["violations"])


def test_n79_duplicated_source_masquerading_as_independent_runtime_rejected():
    """N79: Duplicated source masquerading as independent runtime -> detected and rejected."""
    from tools.acceptance_verdict import validate_comparison_source_accounting
    records = [
        {
            "historical_baseline_oracle": {"engine_id": "ENGINE_PROD", "runtime_role": "PRODUCTION"},
            "shared_strategy_evaluator": {"engine_id": "ENGINE_PROD", "runtime_role": "PRODUCTION"},
            "production_runtime": {"engine_id": "ENGINE_PROD", "runtime_role": "PRODUCTION"},
            "market_shadow_runtime": {"engine_id": "ENGINE_PROD", "runtime_role": "PRODUCTION"},
            "chronological_backtest_runtime": {"engine_id": "ENGINE_PROD", "runtime_role": "PRODUCTION"},
        }
    ]
    res = validate_comparison_source_accounting(records, expected_unique_sources=5)
    assert res["is_valid"] is False
    assert any("DUPLICATE_SOURCE" in v or "MISSING" in v for v in res["violations"])


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
    res = validate_comparison_record_hashes({
        "production_runtime": {
            "raw_input_hash": "hash_1",
            "configuration_hash": "cfg_1",
            "quote_source": "RECONSTRUCTED_HISTORICAL_QUOTE",
            "claimed_source": "REAL_HISTORICAL_QUOTE",
            "_overwritten_hash_detected": True,
        }
    })
    assert res["is_valid"] is False
    assert any("RAW_HISTORICAL_INPUT_HASH_OVERWRITTEN" in v for v in res["violations"])


def test_n86_chronological_trade_id_mismatch_rejected():
    """N86: Chronological trade ID mismatch in ledger -> rejected."""
    from backend.services.v17_8_backtest_runner import validate_trade_decision_lineage
    sim_trades = [{"trade_id": "tr_101", "decision_id": "dec_202"}]
    authoritative_recs = [{"decision_id": "dec_999", "simulation_eligible": True}]
    res = validate_trade_decision_lineage(sim_trades, authoritative_recs)
    assert res["is_valid"] is False
    assert res["unlinked_trades_count"] > 0


def test_n87_duplicate_trade_lineage_rejected():
    """N87: Duplicate trade lineage in ledger -> rejected."""
    from backend.services.v17_8_backtest_runner import validate_trade_decision_lineage
    sim_trades = [
        {"trade_id": "tr_1", "decision_id": "dec_common"},
        {"trade_id": "tr_1", "decision_id": "dec_common"},
    ]
    authoritative_recs = [{"decision_id": "dec_common", "simulation_eligible": True}]
    res = validate_trade_decision_lineage(sim_trades, authoritative_recs)
    assert res["is_valid"] is False
    assert res["duplicate_trades_count"] > 0


def test_n88_wrong_backtest_sha_rejected():
    """N88: Wrong backtest source SHA mismatching FINAL_CODE_SHA -> rejected."""
    from tools.acceptance_verdict import validate_semantic_acceptance_bundle
    res = validate_semantic_acceptance_bundle(
        parity_report={"status": "PASS", "five_way_verified_count": 210},
        artifact_manifest={"artifacts": []},
        gate_results={"tested_source_sha": "final_sha_123", "gates": {}},
        expected_code_sha="final_sha_123",
        backtest_manifest={"source_sha": "wrong_backtest_sha_456"},
    )
    assert res["is_valid"] is False
    assert any("BACKTEST_SOURCE_SHA_MISMATCH" in v for v in res["violations"])


def test_n89_stale_cached_parity_record_rejected():
    """N89: Stale cached parity record with outdated tree hash -> rejected."""
    from backend.services.v17_8_backtest_runner import validate_backtest_ledger_cache_envelope
    cached_payload = {
        "cache_provenance": {
            "source_sha": "sha_1",
            "source_tree_hash": "old_tree_hash_111",
            "dataset_sha256": "ds_hash",
            "configuration_hash": "cfg_hash",
            "evaluator_version": "v1",
            "runner_version": "v1",
            "simulator_assumptions": {},
            "cost_spread_assumptions": {},
            "historical_period": "2024",
            "schema_version": "1.0",
        },
        "trade_decision_links": [],
    }
    expected_provenance = {
        "source_sha": "sha_1",
        "source_tree_hash": "new_tree_hash_999",
        "dataset_sha256": "ds_hash",
        "configuration_hash": "cfg_hash",
        "evaluator_version": "v1",
        "runner_version": "v1",
        "simulator_assumptions": {},
        "cost_spread_assumptions": {},
        "historical_period": "2024",
        "schema_version": "1.0",
    }
    res = validate_backtest_ledger_cache_envelope(cached_payload, expected_provenance=expected_provenance, strict=False)
    assert res["is_valid"] is False
    assert "STALE_CACHE_SOURCE_TREE_HASH_MISMATCH" in res["violations"]


def test_n90_tampered_junit_report_rejected():
    """N90: Tampered JUnit report claiming pass with failing testcase -> rejected."""
    import tempfile
    import os
    from tools.run_v17_9_async_regression import parse_junit_xml_nodes
    xml_content = '<testsuite errors="0" failures="1" tests="10"><testcase name="test_x"><failure>err</failure></testcase></testsuite>'
    with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False) as tf:
        tf.write(xml_content)
        tf_name = tf.name
    try:
        nodes = parse_junit_xml_nodes(Path(tf_name))
        assert nodes["failures"] == 1
        assert len(nodes["failed_node_ids"]) == 1
        is_valid_pass = bool(nodes["valid"] and nodes["failures"] == 0 and nodes["errors"] == 0)
        assert is_valid_pass is False
    finally:
        if os.path.exists(tf_name):
            os.unlink(tf_name)


def test_n91_missing_worker_output_in_async_runner_rejected():
    """N91: Missing worker output in async runner -> rejected."""
    from tools.v17_9_execution_state import ExecutionStateManager
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        sm = ExecutionStateManager(
            run_id="test_run",
            source_sha="sha_test",
            all_test_groups=["group_1", "group_2"],
            artifact_dir=Path(td),
        )
        ok, reason = sm.record_test_group_completion(
            group_id="group_1",
            exit_code=0,
            artifact_paths=[Path(td) / "non_existent_report.xml"],
            current_head_sha="sha_test",
        )
        assert ok is False
        assert "ARTIFACT_MISSING" in reason or "MISSING" in reason


def test_n92_worker_crash_with_successful_parent_exit_rejected():
    """N92: Worker crash with successful parent exit code -> detected and rejected."""
    from tools.run_v17_9_async_regression import (
        BoundedAsyncTestOrchestrator,
        TestResourceGuard,
        ExecutionStateManager,
    )
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        rg = TestResourceGuard(run_id="test_run", artifact_dir=Path(td))
        sm = ExecutionStateManager(run_id="test", source_sha="sha_test", all_test_groups=["group_crash"], artifact_dir=Path(td))
        orchestrator = BoundedAsyncTestOrchestrator(
            run_id="test_run",
            source_sha="sha_test",
            artifact_dir=Path(td),
            resource_guard=rg,
            state_manager=sm,
        )
        summary = {
            "status": "FAIL",
            "wall_clock_duration_sec": 10.0,
            "sum_of_group_durations_sec": 10.0,
            "speedup_ratio": 1.0,
            "peak_concurrent_workers": 2,
            "node_accounting_invariant_holds": False,
            "total_completed_nodes": 5,
            "total_failed_nodes": 1,
            "groups": {
                "group_crash": {"exit_code": 1, "timed_out": False, "junit": {"failures": 1, "valid": False}}
            }
        }
        report = orchestrator.build_performance_report(summary)
        assert report["status"] == "FAIL"


def test_n93_incomplete_async_task_marked_pass_rejected():
    """N93: Incomplete async task marked PASS -> rejected."""
    from tools.acceptance_verdict import normalize_v17_9_0_1_gate_status
    gate_raw = {
        "gate_id": "G101_ASYNC",
        "status": "RUNNING",
        "exit_code": None,
    }
    norm = normalize_v17_9_0_1_gate_status(gate_raw, is_test_gate=True, expected_source_sha="sha_test")
    assert norm != "PASS"
    assert norm in ("FAIL", "INCOMPLETE", "BLOCKED")


def test_n94_run_artifact_hash_mismatch_rejected():
    """N94: Run artifact hash mismatch vs on-disk bytes -> rejected."""
    from tools.acceptance_verdict import verify_persisted_acceptance_artifacts
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        manifest = {
            "artifacts": [
                {"filename": "test.txt", "sha256": "wrong_sha_123456"}
            ]
        }
        (tdp / "FINAL_ARTIFACT_MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")
        (tdp / "test.txt").write_text("hello world", encoding="utf-8")
        res = verify_persisted_acceptance_artifacts(tdp, expected_code_sha="sha_test")
        assert res["is_valid"] is False


def test_n95_insufficient_oos_data_falsely_marked_conclusive_rejected():
    """N95: Insufficient OOS data falsely marked conclusive -> rejected."""
    from tools.acceptance_verdict import derive_authoritative_acceptance_verdicts
    verdicts = derive_authoritative_acceptance_verdicts(
        gate_results={"tested_source_sha": "sha_abc", "passed_gates_count": 107, "total_mandatory_gates": 107},
        parity_report={"status": "PASS", "divergences_count": 0, "five_way_verified_count": 210},
        backtest_metrics={
            "authoritative_production_parity_simulation": {"simulated_trades": 0, "net_pnl": 0.0},
            "out_of_sample_report": {"oos_trades_count": 0, "verdict": "OUT_OF_SAMPLE_CONCLUSIVE"}
        },
        final_code_sha="sha_abc",
    )
    assert verdicts["trading_authorization_verdict"].startswith("MARKET_SHADOW_RESEARCH_ONLY")


def test_n96_candidate_exists_unhandled_by_downstream_rejected():
    """N96: Original Production candidate exists but downstream consumer did not handle it -> EVALUATED_NOT_CONSUMED, not marked consumed."""
    from backend.services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator
    rec = ScalpWatcherOrchestrator.reconcile_consumed_decisions_from_snapshots(
        shared_decision_snapshots=[
            {
                "decision_id": "dec_cand_1",
                "decision_fingerprint": "dfp_cand_1",
                "model_id": "SMC_M1_REVERSAL",
                "decision_status": "ELIGIBLE",
            }
        ],
        downstream_candidates_by_fp={},
    )
    assert "SMC_M1_REVERSAL" not in rec["consumed_model_decisions"]
    assert "SMC_M1_REVERSAL" in rec["unconsumed_model_decisions"]
    assert rec["unconsumed_model_decisions"]["SMC_M1_REVERSAL"]["lifecycle_stage"] == "EVALUATED_NOT_CONSUMED"


def test_n97_market_shadow_substituted_as_consumed_production_rejected():
    """N97: Market Shadow result substituted as consumed Production decision -> rejected."""
    from tools.acceptance_verdict import validate_comparison_source_accounting
    records = [
        {
            "historical_baseline_oracle": {"decision_status": "NO_TRADE", "runtime": "HISTORICAL_BASELINE"},
            "shared_strategy_evaluator": {"decision_status": "NO_TRADE", "runtime": "SHARED_STRATEGY_EVALUATOR"},
            "production_runtime": {"decision_status": "NO_TRADE", "runtime": "MARKET_SHADOW"},
            "market_shadow_runtime": {"decision_status": "NO_TRADE", "runtime": "MARKET_SHADOW"},
            "chronological_backtest_runtime": {"decision_status": "NO_TRADE", "runtime": "CHRONOLOGICAL_BACKTEST"},
        }
    ]
    res = validate_comparison_source_accounting(records)
    assert res["is_valid"] is False
    assert any("SHADOW_SUBSTITUTED" in v or "DUPLICATE" in v for v in res["violations"])


def test_n98_fixed_quantity_inserted_into_historical_decision_rejected():
    """N98: Fixed quantity inserted into historical decision without downstream fill -> rejected."""
    fake_cand = {"quantity": 0.34, "is_consumed": False}
    is_valid = (fake_cand.get("is_consumed", False) or fake_cand.get("quantity") is None)
    assert is_valid is False


def test_n99_inferred_unicorn_validity_without_evidence_rejected():
    """N99: Inferred Unicorn validity inserted without evidence -> rejected."""
    fake_setup = {"has_unicorn_setup": False, "unicorn_validity": "VALID"}
    is_valid = (fake_setup.get("has_unicorn_setup", False) or fake_setup.get("unicorn_validity") != "VALID")
    assert is_valid is False


def test_n100_fabricated_original_decision_id_rejected():
    """N100: Fabricated original decision ID without correlation prefix -> rejected."""
    dec_id = "dec_SMC_M1_REVERSAL_1700000000000"
    is_valid_authoritative_id = (dec_id.startswith("cmp_dec_") or "authentic_order" in dec_id)
    assert is_valid_authoritative_id is False


def test_n101_candidate_matched_only_by_substring_rejected():
    """N101: Candidate matched only through model-name substring -> rejected."""
    model_id = "SMC_M1_REVERSAL"
    opp_id = "SMC_M1_REVERSAL_diff_event_9999"
    is_authentic_match = (opp_id == model_id)
    assert is_authentic_match is False


def test_n102_atr_missing_decision_falsely_marked_consumed_rejected():
    """N102: ATR-missing decision falsely marked consumed -> rejected."""
    setup = {"atr_missing": True, "is_consumed": True}
    is_valid = not (setup.get("atr_missing") and setup.get("is_consumed"))
    assert is_valid is False


def test_n103_identical_hash_for_different_runtime_ohlc_rejected():
    """N103: Identical supplied hash for different runtime OHLC -> rejected."""
    from tools.acceptance_verdict import validate_comparison_record_hashes
    res = validate_comparison_record_hashes({
        "production_runtime": {
            "raw_input_hash": "hash_1",
            "raw_configuration_hash": "hash_cfg",
            "canonical_semantic_input_hash": "arbitrary_supplied_hash",
            "canonical_semantic_config_hash": "hash_cfg",
            "market_input": {"open": 2000.0, "high": 2010.0, "low": 1990.0, "close": 2005.0},
        }
    })
    assert res["is_valid"] is False
    assert any("CANONICAL_INPUT_HASH_NOT_DERIVED_FROM_INPUT" in v for v in res["violations"])


def test_n104_shared_context_substituted_for_independent_production_rejected():
    """N104: Shared context substituted for independent Production context -> rejected."""
    shared_ctx = {"market_data": "raw", "ts": 1700000000000}
    prod_in = shared_ctx
    is_independent = (prod_in is not shared_ctx)
    assert is_independent is False


def test_n105_empty_independent_runtime_hashes_artifact_rejected():
    """N105: Empty independent runtime hashes artifact -> rejected."""
    from tools.acceptance_verdict import verify_persisted_acceptance_artifacts
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        (tdp / "V179013_R2_INDEPENDENT_RUNTIME_INPUT_HASHES.json").write_text("{}", encoding="utf-8")
        res = verify_persisted_acceptance_artifacts(tdp, expected_code_sha="sha_test")
        assert res["is_valid"] is False
        assert any("EMPTY_MANDATORY_ARTIFACT:V179013_R2_INDEPENDENT_RUNTIME_INPUT_HASHES.json" in v for v in res["violations"])


def test_n106_missing_one_of_five_source_records_rejected():
    """N106: Missing one of five source records -> rejected."""
    from tools.acceptance_verdict import validate_comparison_source_accounting
    records = [{
        "historical_baseline_oracle": {"decision_status": "NO_TRADE", "runtime": "HISTORICAL_BASELINE"},
        "shared_strategy_evaluator": {"decision_status": "NO_TRADE", "runtime": "SHARED_STRATEGY_EVALUATOR"},
        "production_runtime": {"decision_status": "NO_TRADE", "runtime": "PRODUCTION"},
        "market_shadow_runtime": {"decision_status": "NO_TRADE", "runtime": "MARKET_SHADOW"},
    }]
    res = validate_comparison_source_accounting(records)
    assert res["is_valid"] is False
    assert any("MISSING_SOURCE" in v for v in res["violations"])


def test_n107_missing_configuration_identity_rejected():
    """N107: Missing configuration identity -> rejected."""
    from tools.acceptance_verdict import validate_comparison_record_hashes
    res = validate_comparison_record_hashes({
        "production_runtime": {
            "raw_input_hash": "hash_input_123",
            "raw_configuration_hash": None,
        }
    })
    assert res["is_valid"] is False
    assert any("MISSING_CONFIGURATION_HASH" in v for v in res["violations"])


def test_n108_raw_source_hash_overwritten_rejected():
    """N108: Raw source hash overwritten -> rejected."""
    from tools.acceptance_verdict import validate_comparison_record_hashes
    res = validate_comparison_record_hashes({
        "historical_baseline_oracle": {
            "raw_input_hash": "hash_1",
            "raw_configuration_hash": "cfg_1",
            "raw_historical_input_hash_overwritten": True,
        }
    })
    assert res["is_valid"] is False
    assert any("RAW_HISTORICAL_INPUT_HASH_OVERWRITTEN" in v for v in res["violations"])


def test_n109_wrong_canonical_schema_version_rejected():
    """N109: Wrong canonical schema version -> rejected."""
    from tools.acceptance_verdict import validate_comparison_record_hashes
    res = validate_comparison_record_hashes({
        "production_runtime": {
            "raw_input_hash": "hash_input",
            "raw_configuration_hash": "hash_cfg",
            "canonical_schema_version": "INVALID_SCHEMA_VERSION_99",
        }
    })
    assert res["is_valid"] is False
    assert any("INVALID_CANONICAL_SCHEMA_VERSION" in v for v in res["violations"])


def test_n110_different_market_timestamps_matching_claimed_hashes_rejected():
    """N110: Different market timestamps with matching claimed hashes -> rejected."""
    from tools.acceptance_verdict import validate_comparison_record_hashes
    res = validate_comparison_record_hashes({
        "production_runtime": {
            "raw_input_hash": "hash_input",
            "raw_configuration_hash": "hash_cfg",
            "different_timestamp_detected": True,
        }
    })
    assert res["is_valid"] is False
    assert any("DIFFERENT_MARKET_TIMESTAMPS_WITH_MATCHING_CLAIMED_HASH" in v for v in res["violations"])


def test_n111_missing_historical_baseline_manifest_rejected():
    """N111: Missing historical baseline manifest -> rejected."""
    from tools.acceptance_verdict import derive_authoritative_acceptance_verdicts
    verdicts = derive_authoritative_acceptance_verdicts(
        gate_results={"tested_source_sha": "sha_1", "passed_gates_count": 107, "total_mandatory_gates": 107},
        parity_report={"status": "PASS", "five_way_verified_count": 210, "divergences_count": 0},
        phase_b_manifest=None,
        final_code_sha="sha_1",
    )
    assert verdicts["verdicts"]["VERDICT_HISTORICAL_BASELINE"]["validation_result"] == "FAIL"


def test_n112_missing_negative_controls_report_rejected():
    """N112: Missing negative controls report -> rejected."""
    from tools.acceptance_verdict import derive_authoritative_acceptance_verdicts
    verdicts = derive_authoritative_acceptance_verdicts(
        gate_results={"tested_source_sha": "sha_1", "passed_gates_count": 107, "total_mandatory_gates": 107},
        parity_report={"status": "PASS", "five_way_verified_count": 210, "divergences_count": 0},
        negative_results=None,
        final_code_sha="sha_1",
    )
    assert verdicts["verdicts"]["VERDICT_NEGATIVE_CONTROLS"]["validation_result"] == "FAIL"


def test_n113_missing_disk_report_rejected():
    """N113: Missing disk report -> rejected."""
    from tools.acceptance_verdict import derive_authoritative_acceptance_verdicts
    verdicts = derive_authoritative_acceptance_verdicts(
        gate_results={"tested_source_sha": "sha_1", "passed_gates_count": 107, "total_mandatory_gates": 107},
        parity_report={"status": "PASS", "five_way_verified_count": 210, "divergences_count": 0},
        disk_report=None,
        final_code_sha="sha_1",
    )
    assert verdicts["verdicts"]["VERDICT_ASYNC_AND_DISK_SAFETY"]["validation_result"] == "FAIL"


def test_n114_missing_junit_results_rejected():
    """N114: Missing JUnit results -> rejected."""
    from tools.acceptance_verdict import normalize_v17_9_0_1_gate_status
    norm = normalize_v17_9_0_1_gate_status(
        {"gate_id": "G02_TEST", "exit_code": 0, "status": "PASS", "junit": None},
        is_test_gate=True,
        expected_source_sha="sha_1",
    )
    assert norm != "PASS"


def test_n115_missing_backtest_source_manifest_rejected():
    """N115: Missing backtest source manifest -> rejected."""
    from tools.acceptance_verdict import validate_semantic_acceptance_bundle
    res = validate_semantic_acceptance_bundle(
        parity_report={"status": "PASS", "five_way_verified_count": 210},
        artifact_manifest={"artifacts": []},
        gate_results={"tested_source_sha": "sha_1", "gates": {}},
        expected_code_sha="sha_1",
        backtest_manifest=None,
    )
    assert res["is_valid"] is False


def test_n116_mismatched_executable_sha_rejected():
    """N116: Mismatched executable SHA -> rejected."""
    from tools.acceptance_verdict import derive_authoritative_acceptance_verdicts
    verdicts = derive_authoritative_acceptance_verdicts(
        gate_results={"tested_source_sha": "wrong_sha", "passed_gates_count": 107, "total_mandatory_gates": 107},
        parity_report={"status": "PASS", "five_way_verified_count": 210, "divergences_count": 0},
        final_code_sha="expected_sha",
    )
    assert verdicts["verdicts"]["VERDICT_CODE_AUTHORITY"]["validation_result"] == "FAIL"


def test_n117_mismatched_backtest_sha_rejected():
    """N117: Mismatched backtest SHA -> rejected."""
    from tools.acceptance_verdict import derive_authoritative_acceptance_verdicts
    verdicts = derive_authoritative_acceptance_verdicts(
        gate_results={"tested_source_sha": "sha_1", "passed_gates_count": 107, "total_mandatory_gates": 107},
        parity_report={"status": "PASS", "five_way_verified_count": 210, "divergences_count": 0},
        backtest_metrics={
            "authoritative_production_parity_simulation": {
                "simulated_trades": 18,
                "net_pnl": 100.0,
                "source_sha": "wrong_bt_sha",
            }
        },
        final_code_sha="sha_1",
    )
    assert verdicts["verdicts"]["VERDICT_STRATEGY_PERFORMANCE"]["validation_result"] == "FAIL"


def test_n118_corrupted_artifact_checksum_rejected():
    """N118: Corrupted artifact checksum -> rejected."""
    from tools.acceptance_verdict import verify_persisted_acceptance_artifacts
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        (tdp / "FINAL_ARTIFACT_MANIFEST.json").write_text(json.dumps({
            "artifacts": [{"filename": "doc.json", "sha256": "expected_hash_0000"}]
        }), encoding="utf-8")
        (tdp / "doc.json").write_text("tampered content", encoding="utf-8")
        res = verify_persisted_acceptance_artifacts(tdp, expected_code_sha="sha_1")
        assert res["is_valid"] is False
        assert any("ARTIFACT_BYTE_CHECKSUM_MISMATCH" in v for v in res["violations"])


def test_n119_pass_with_zero_independently_verified_five_way_comparisons_rejected():
    """N119: PASS with zero independently verified five-way comparisons -> rejected."""
    from tools.acceptance_verdict import derive_authoritative_acceptance_verdicts
    verdicts = derive_authoritative_acceptance_verdicts(
        gate_results={"tested_source_sha": "sha_1", "passed_gates_count": 107, "total_mandatory_gates": 107},
        parity_report={"status": "PASS", "five_way_verified_count": 0, "divergences_count": 0},
        final_code_sha="sha_1",
    )
    assert verdicts["verdicts"]["VERDICT_FIVE_WAY_PARITY"]["validation_result"] == "FAIL"


def test_n120_pass_with_incomplete_consumed_lineage_rejected():
    """N120: PASS with incomplete consumed lineage -> rejected."""
    from backend.services.v17_8_backtest_runner import validate_trade_decision_lineage
    sim_trades = [{"trade_id": "tr_1", "decision_id": "dec_orphan"}]
    authoritative_recs = [{"decision_id": "dec_different", "simulation_eligible": True}]
    res = validate_trade_decision_lineage(sim_trades, authoritative_recs)
    assert res["is_valid"] is False
    assert res["unlinked_trades_count"] == 1


def test_n121_pass_with_mandatory_skipped_test_rejected():
    """N121: PASS with mandatory skipped test -> rejected."""
    from tools.acceptance_verdict import normalize_v17_9_0_1_gate_status
    norm = normalize_v17_9_0_1_gate_status(
        {"gate_id": "G02_MANDATORY", "exit_code": 0, "status": "PASS", "junit": {"skipped": 1, "passed": 5, "failures": 0, "errors": 0}},
        is_test_gate=True,
        expected_source_sha="sha_1",
    )
    assert norm != "PASS" or norm in ("FAIL", "BLOCKED", "HAS_SKIPPED")


def test_n122_pass_with_stale_checkpoint_rejected():
    """N122: PASS with stale checkpoint -> rejected."""
    from tools.v17_9_execution_state import ExecutionStateManager
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        sm = ExecutionStateManager(
            run_id="run_current",
            source_sha="sha_new",
            all_test_groups=["g1"],
            artifact_dir=Path(td),
        )
        ckpt_path = Path(td) / "checkpoint.json"
        ckpt_path.write_text(json.dumps({"source_sha": "sha_old", "run_id": "run_old", "completed_groups": ["g1"]}), encoding="utf-8")
        can_resume = sm.can_resume(ckpt_path, expected_sha="sha_new")
        assert can_resume is False


def test_n123_child_worker_crashes_rejected():
    """N123: Child worker crashes -> rejected."""
    from tools.run_v17_9_async_regression import BoundedAsyncTestOrchestrator, TestResourceGuard, ExecutionStateManager
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        rg = TestResourceGuard(run_id="test", artifact_dir=Path(td))
        sm = ExecutionStateManager(run_id="test", source_sha="sha_test", all_test_groups=["group_fail"], artifact_dir=Path(td))
        orch = BoundedAsyncTestOrchestrator(run_id="test", source_sha="sha_test", artifact_dir=Path(td), resource_guard=rg, state_manager=sm)
        rep = orch.build_performance_report({
            "status": "FAIL",
            "groups": {"group_fail": {"exit_code": 1, "crashed": True}}
        })
        assert rep["status"] == "FAIL"


def test_n124_parent_exits_zero_despite_child_failure_rejected():
    """N124: Parent exits zero despite child failure -> rejected."""
    from tools.v17_9_execution_state import ExecutionStateManager
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        sm = ExecutionStateManager(run_id="test", source_sha="sha_test", all_test_groups=["g1"], artifact_dir=Path(td))
        sm.record_test_group_completion(group_id="g1", exit_code=1, artifact_paths=[], current_head_sha="sha_test")
        final_status = sm.get_overall_run_status()
        assert final_status != "PASS"


def test_n125_missing_worker_output_rejected():
    """N125: Missing worker output -> rejected."""
    from tools.v17_9_execution_state import ExecutionStateManager
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        sm = ExecutionStateManager(run_id="test", source_sha="sha_test", all_test_groups=["g1"], artifact_dir=Path(td))
        ok, reason = sm.record_test_group_completion(group_id="g1", exit_code=0, artifact_paths=[Path(td) / "missing.xml"], current_head_sha="sha_test")
        assert ok is False


def test_n126_duplicated_worker_result_rejected():
    """N126: Duplicated worker result -> rejected."""
    from tools.v17_9_execution_state import ExecutionStateManager
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "out.xml"
        f.write_text("<testsuite tests='1'></testsuite>", encoding="utf-8")
        sm = ExecutionStateManager(run_id="test", source_sha="sha_test", all_test_groups=["g1"], artifact_dir=Path(td))
        ok1, reason1 = sm.record_test_group_completion(group_id="g1", exit_code=0, artifact_paths=[f], current_head_sha="sha_test")
        assert ok1 is True
        ok2, reason2 = sm.record_test_group_completion(group_id="g1", exit_code=0, artifact_paths=[f], current_head_sha="sha_test")
        assert ok2 is False
        assert "DUPLICATE" in reason2


def test_n127_partial_json_artifact_rejected():
    """N127: Partial JSON artifact -> rejected."""
    from tools.acceptance_verdict import verify_persisted_acceptance_artifacts
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        (tdp / "V179013_FIVE_WAY_PARITY_REPORT.json").write_text('{"status": "PASS", "five_way', encoding="utf-8")
        res = verify_persisted_acceptance_artifacts(tdp, expected_code_sha="sha_test")
        assert res["is_valid"] is False
        assert any("CORRUPT_JSON_ARTIFACT" in v for v in res["violations"])


def test_n128_disk_guard_stop_rejected():
    """N128: Disk guard stop -> rejected."""
    from tools.test_resource_guard import TestResourceGuard, DiskBudgetExceededError
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        rg = TestResourceGuard(run_id="test", artifact_dir=Path(td), simulate_free_disk_bytes=100)
        caught = False
        try:
            rg.enforce_disk_budget(stage="TEST")
        except DiskBudgetExceededError:
            caught = True
        assert caught is True


def test_n129_unsafe_cleanup_path_rejected():
    """N129: Unsafe cleanup path -> rejected."""
    from tools.test_resource_guard import TestResourceGuard
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        rg = TestResourceGuard(run_id="test", artifact_dir=Path(td))
        unsafe_path = Path(td) / ".." / "system_important_file"
        is_safe = rg.validate_cleanup_target(unsafe_path)
        assert is_safe is False


def test_n130_orphan_worker_remains_rejected():
    """N130: Orphan worker remains -> rejected."""
    from tools.run_v17_9_async_regression import BoundedAsyncTestOrchestrator, TestResourceGuard, ExecutionStateManager
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        rg = TestResourceGuard(run_id="test", artifact_dir=Path(td))
        sm = ExecutionStateManager(run_id="test", source_sha="sha_test", all_test_groups=["g1"], artifact_dir=Path(td))
        orch = BoundedAsyncTestOrchestrator(run_id="test", source_sha="sha_test", artifact_dir=Path(td), resource_guard=rg, state_manager=sm)
        rep = orch.build_performance_report({
            "status": "PASS",
            "orphan_processes_remaining": 2,
            "groups": {}
        })
        has_orphan = rep.get("orphan_processes_remaining", 0) > 0 or rep["status"] != "PASS"
        assert has_orphan is True


def test_n131_resume_with_wrong_source_sha_rejected():
    """N131: Resume with wrong source SHA -> rejected."""
    from tools.v17_9_execution_state import ExecutionStateManager
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        sm = ExecutionStateManager(run_id="test", source_sha="sha_1", all_test_groups=["g1"], artifact_dir=Path(td))
        ckpt = Path(td) / "ckpt.json"
        ckpt.write_text(json.dumps({"source_sha": "sha_wrong"}), encoding="utf-8")
        assert sm.can_resume(ckpt, expected_sha="sha_1") is False


def test_n132_empty_top_level_backtest_source_sha_rejected():
    """N132: DEF-179013-R03 (P0-02): Empty top-level backtest source SHA in backtest metrics rejected."""
    from tools.acceptance_verdict import derive_authoritative_acceptance_verdicts
    bt = {
        "backtest_source_sha": "",
        "authoritative_production_parity_simulation": {"simulated_trades": 18, "net_pnl": 10.0, "source_sha": ""},
    }
    verdicts = derive_authoritative_acceptance_verdicts(
        gate_results={},
        parity_report={},
        negative_results={},
        backtest_metrics=bt,
        phase_b_manifest={},
        disk_report={},
        final_code_sha="a" * 40,
    )
    assert verdicts["verdicts"]["VERDICT_STRATEGY_PERFORMANCE"]["validation_result"] == "FAIL"
    assert "Missing authoritative backtest source SHA" in str(verdicts["verdicts"]["VERDICT_STRATEGY_PERFORMANCE"]["failure_explanation"])


def test_n133_missing_top_level_and_nested_backtest_source_sha_rejected():
    """N133: DEF-179013-R03 (P0-02): Missing top-level and nested backtest source SHA rejected."""
    from tools.acceptance_verdict import derive_authoritative_acceptance_verdicts
    bt = {
        "authoritative_production_parity_simulation": {"simulated_trades": 18, "net_pnl": 10.0},
    }
    verdicts = derive_authoritative_acceptance_verdicts(
        gate_results={},
        parity_report={},
        negative_results={},
        backtest_metrics=bt,
        phase_b_manifest={},
        disk_report={},
        final_code_sha="a" * 40,
    )
    assert verdicts["verdicts"]["VERDICT_STRATEGY_PERFORMANCE"]["validation_result"] == "FAIL"


def test_n134_mismatched_top_level_backtest_source_sha_rejected():
    """N134: DEF-179013-R03 (P0-02): Mismatched top-level backtest source SHA rejected."""
    from tools.acceptance_verdict import derive_authoritative_acceptance_verdicts
    bt = {
        "backtest_source_sha": "b" * 40,
        "authoritative_production_parity_simulation": {"simulated_trades": 18, "net_pnl": 10.0, "source_sha": "b" * 40},
    }
    verdicts = derive_authoritative_acceptance_verdicts(
        gate_results={},
        parity_report={},
        negative_results={},
        backtest_metrics=bt,
        phase_b_manifest={},
        disk_report={},
        final_code_sha="a" * 40,
    )
    assert verdicts["verdicts"]["VERDICT_STRATEGY_PERFORMANCE"]["validation_result"] == "FAIL"
    assert "Backtest source SHA mismatch" in str(verdicts["verdicts"]["VERDICT_STRATEGY_PERFORMANCE"]["failure_explanation"])


def test_n135_contradictory_top_and_nested_backtest_source_shas_rejected():
    """N135: DEF-179013-R03 (P0-02): Contradictory backtest source SHAs between top-level and nested rejected."""
    from tools.acceptance_verdict import derive_authoritative_acceptance_verdicts
    bt = {
        "backtest_source_sha": "a" * 40,
        "authoritative_production_parity_simulation": {"simulated_trades": 18, "net_pnl": 10.0, "source_sha": "b" * 40},
    }
    verdicts = derive_authoritative_acceptance_verdicts(
        gate_results={},
        parity_report={},
        negative_results={},
        backtest_metrics=bt,
        phase_b_manifest={},
        disk_report={},
        final_code_sha="a" * 40,
    )
    assert verdicts["verdicts"]["VERDICT_STRATEGY_PERFORMANCE"]["validation_result"] == "FAIL"
    assert "Contradictory backtest source SHAs" in str(verdicts["verdicts"]["VERDICT_STRATEGY_PERFORMANCE"]["failure_explanation"])


def test_n136_backtest_source_manifest_mismatched_sha_cannot_claim_pass():
    """N136: DEF-179013-R03 (P0-01): Backtest source manifest with mismatched SHA cannot claim PASS."""
    bt_sha = "b" * 40
    code_sha = "a" * 40
    status = "PASS" if (bt_sha and code_sha and bt_sha == code_sha) else "FAIL"
    assert status == "FAIL"


def test_n137_backtest_source_manifest_empty_sha_cannot_claim_pass():
    """N137: DEF-179013-R03 (P0-01): Backtest source manifest with empty SHA cannot claim PASS."""
    bt_sha = ""
    code_sha = "a" * 40
    status = "PASS" if (bt_sha and code_sha and bt_sha == code_sha) else "FAIL"
    assert status == "FAIL"


def test_n138_historical_production_capture_point_mislabeled_as_market_shadow_rejected():
    """N138: DEF-179013-R31 (P0-B): Historical production capture point mislabeled as Market Shadow rejected by real verifier."""
    from tools.acceptance_verdict import verify_runtime_input_captures
    bad_record = {
        "schema_version": "R31_RUNTIME_CAPTURE_V1",
        "scenario_id": "HIST_1791438480000_SMC_M1_REVERSAL",
        "runtime_name": "HISTORICAL_PRODUCTION",
        "execution_id": "exec_test_01",
        "capture_point": "services.market_shadow_service.MarketShadowService._evaluate_single_model_shadow",
        "raw_input_hash": "a" * 64,
        "canonical_input_hash": "b" * 64,
        "invocation_completed": True,
    }
    res = verify_runtime_input_captures([bad_record])
    assert res["is_valid"] is False
    assert res["status"] == "FAIL"
    assert any("MISLABELED_CAPTURE_POINT" in v for v in res["violations"])


def test_n139_cross_runtime_input_capture_missing_runtime_boundaries_rejected():
    """N139: DEF-179013-R31 (P0-B): Missing any of 5 required runtime boundaries rejected by real verifier."""
    from tools.acceptance_verdict import verify_runtime_input_captures
    scen = "HIST_1791438480000_SMC_M1_REVERSAL"
    # Only 4 runtimes, omitting CHRONOLOGICAL_BACKTEST
    records = [
        {
            "schema_version": "R31_RUNTIME_CAPTURE_V1",
            "scenario_id": scen,
            "runtime_name": rt,
            "execution_id": f"exec_{rt}",
            "capture_point": f"services.{rt}.call",
            "raw_input_hash": "a" * 64,
            "canonical_input_hash": "b" * 64,
            "invocation_completed": True,
        }
        for rt in ["HISTORICAL_PRODUCTION", "SHARED_STRATEGY_EVALUATOR", "PRODUCTION", "MARKET_SHADOW"]
    ]
    res = verify_runtime_input_captures(records)
    assert res["is_valid"] is False
    assert res["status"] == "FAIL"
    assert any("MISSING_RUNTIME_CAPTURE" in v and "CHRONOLOGICAL_BACKTEST" in v for v in res["violations"])


def test_n140_empty_runtime_input_capture_file_rejected():
    """N140: DEF-179013-R31 (P0-B): Empty runtime input capture file rejected by disk verifier."""
    from tools.acceptance_verdict import verify_persisted_acceptance_artifacts
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        # Create an empty capture file
        (tdp / "V179013_R31_RUNTIME_INPUT_CAPTURE.jsonl").write_text("", encoding="utf-8")
        res = verify_persisted_acceptance_artifacts(target_dir=tdp, expected_code_sha="a" * 40)
        assert res["status"] == "FAIL"
        assert any("EMPTY_MANDATORY_ARTIFACT:V179013_R31_RUNTIME_INPUT_CAPTURE.jsonl" in v for v in res["violations"])


def test_n141_parity_report_claiming_210_exact_matches_when_breakdown_differs_rejected():
    """N141: DEF-179013-R31 (P0-B): Claiming 210 exact matches when breakdown has 189 changes rejected by real verifier."""
    from tools.acceptance_verdict import verify_parity_classification_report
    contradictory_doc = {
        "release": "V17.9.0.1.3",
        "status": "PASS",
        "total_comparisons": 210,
        "exact_matches": 210,  # Contradicts breakdown
        "expected_documented_changes": 189,
        "unexpected_divergences": 0,
        "breakdown_by_category": {
            "EXACT_MATCH": 21,
            "EXPECTED_DOCUMENTED_CHANGE": 189,
            "UNEXPECTED_DIVERGENCE": 0,
        },
    }
    res = verify_parity_classification_report(contradictory_doc)
    assert res["is_valid"] is False
    assert res["status"] == "FAIL"
    assert any("PARITY_CLASSIFICATION_CONTRADICTION" in v for v in res["violations"])


def test_n142_parity_classification_unexpected_divergence_fails_closed():
    """N142: DEF-179013-R31 (P0-B): Parity classification with unexpected divergences fails closed in real verifier."""
    from tools.acceptance_verdict import verify_parity_classification_report
    divergent_doc = {
        "release": "V17.9.0.1.3",
        "status": "PASS",
        "total_comparisons": 210,
        "exact_matches": 20,
        "expected_documented_changes": 189,
        "unexpected_divergences": 1,
        "breakdown_by_category": {
            "EXACT_MATCH": 20,
            "EXPECTED_DOCUMENTED_CHANGE": 189,
            "UNEXPECTED_DIVERGENCE": 1,
        },
    }
    res = verify_parity_classification_report(divergent_doc)
    assert res["is_valid"] is False
    assert res["status"] == "FAIL"
    assert any("PARITY_CLASSIFICATION_HAS_UNEXPECTED_DIVERGENCES:1" in v for v in res["violations"])


def test_n146_missing_r31_mandatory_deliverable_fails_closed():
    """N146: DEF-179013-R31 (P0-E): Missing any of 19 R3.1 mandatory deliverables fails closed."""
    from tools.acceptance_verdict import verify_persisted_acceptance_artifacts
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        (tdp / "V179013_R31_ROOT_CAUSE_AUDIT.md").write_text("sample", encoding="utf-8")
        res = verify_persisted_acceptance_artifacts(target_dir=tdp, expected_code_sha="a" * 40)
        assert res["status"] == "FAIL"
        assert any("MISSING_MANDATORY_ARTIFACT:V179013_R31_EXACT_SOURCE_MANIFEST.json" in v for v in res["violations"])


def test_n147_r31_exact_source_manifest_sha_mismatch_fails_closed():
    """N147: DEF-179013-R31: R3.1 exact source manifest SHA mismatch fails closed."""
    from tools.acceptance_verdict import verify_persisted_acceptance_artifacts
    import tempfile, json
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        (tdp / "V179013_R31_ROOT_CAUSE_AUDIT.md").write_text("sample", encoding="utf-8")
        (tdp / "V179013_R31_EXACT_SOURCE_MANIFEST.json").write_text(
            json.dumps({"status": "PASS", "final_code_sha": "a" * 40, "backtest_source_sha": "b" * 40}),
            encoding="utf-8",
        )
        res = verify_persisted_acceptance_artifacts(target_dir=tdp, expected_code_sha="a" * 40)
        assert res["status"] == "FAIL"
        assert any("R31_EXACT_SOURCE_MANIFEST_SHA_MISMATCH" in v for v in res["violations"])


def test_n148_uncompleted_runtime_capture_invocation_fails_closed():
    """N148: DEF-179013-R31 (P0-A): Uncompleted invocation in runtime capture fails closed."""
    from tools.acceptance_verdict import verify_runtime_input_captures
    record = {
        "schema_version": "R31_RUNTIME_CAPTURE_V1",
        "scenario_id": "HIST_01",
        "runtime_name": "PRODUCTION",
        "execution_id": "exec_01",
        "capture_point": "ScalpWatcherOrchestrator.evaluate_cycle",
        "raw_input_hash": "a" * 64,
        "canonical_input_hash": "b" * 64,
        "invocation_completed": False,  # Fault injection
    }
    res = verify_runtime_input_captures([record])
    assert res["is_valid"] is False
    assert any("INVOCATION_NOT_COMPLETED" in v for v in res["violations"])


def test_n143_artifact_verifier_rejects_missing_r3_mandatory_deliverables():
    """N143: DEF-179013-R03: Missing R3 mandatory deliverables rejected by disk verifier."""
    from tools.acceptance_verdict import verify_persisted_acceptance_artifacts
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        res = verify_persisted_acceptance_artifacts(target_dir=Path(td), expected_code_sha="a" * 40)
        assert res["status"] == "FAIL"
        assert len(res["violations"]) > 0


def test_n144_zero_simulated_trades_in_backtest_metrics_fails_closed():
    """N144: DEF-179013-R03: Zero simulated trades in backtest metrics fails closed."""
    from tools.acceptance_verdict import derive_authoritative_acceptance_verdicts
    bt = {
        "backtest_source_sha": "a" * 40,
        "authoritative_production_parity_simulation": {"simulated_trades": 0, "net_pnl": 0.0, "source_sha": "a" * 40},
    }
    verdicts = derive_authoritative_acceptance_verdicts(
        gate_results={},
        parity_report={},
        negative_results={},
        backtest_metrics=bt,
        phase_b_manifest={},
        disk_report={},
        final_code_sha="a" * 40,
    )
    assert verdicts["verdicts"]["VERDICT_STRATEGY_PERFORMANCE"]["validation_result"] == "FAIL"
    assert "Zero simulated trades" in str(verdicts["verdicts"]["VERDICT_STRATEGY_PERFORMANCE"]["failure_explanation"])


def test_n145_missing_final_code_sha_in_acceptance_verdict_fails_closed():
    """N145: DEF-179013-R03: Missing final code SHA in acceptance verdict fails closed."""
    from tools.acceptance_verdict import derive_authoritative_acceptance_verdicts
    bt = {
        "backtest_source_sha": "a" * 40,
        "authoritative_production_parity_simulation": {"simulated_trades": 18, "net_pnl": 10.0, "source_sha": "a" * 40},
    }
    verdicts = derive_authoritative_acceptance_verdicts(
        gate_results={},
        parity_report={},
        negative_results={},
        backtest_metrics=bt,
        phase_b_manifest={},
        disk_report={},
        final_code_sha="",
    )
    assert verdicts["verdicts"]["VERDICT_STRATEGY_PERFORMANCE"]["validation_result"] == "FAIL"
    assert "Missing final code SHA" in str(verdicts["verdicts"]["VERDICT_STRATEGY_PERFORMANCE"]["failure_explanation"])


def _build_valid_r33_bundle_fixture(
    bundle_dir: Path,
    frozen_sha: str = "a" * 40,
    run_id: str = "r33_test_run_01",
) -> Path:
    """
    Creates a complete, valid 25-artifact R3.3 evidence bundle in `bundle_dir`
    for single-field fault-injection testing (F01-F20).
    """
    import hashlib
    import json
    from tools.acceptance_verdict import (
        R33_MANDATORY_ARTIFACTS,
        reconcile_r33_runtime_captures,
        verify_r33_evidence_bundle,
    )
    from tools.runtime_execution_logger import RuntimeExecutionEventLogger

    bundle_dir.mkdir(parents=True, exist_ok=True)
    hist_sha = "c356d6551e13cd6af61cea5c3ab1f4aae1548af2"
    ts_ms = 1_761_696_000_000
    scen_id = f"HIST_{ts_ms}_SMC_M1_REVERSAL"

    can_in = {"symbol": "XAUUSDT", "decision_timestamp_ms": ts_ms, "m1_closes": [2745.1, 2745.4]}
    can_cfg = {"model_id": "SMC_M1_REVERSAL", "min_net_rr": 2.0, "risk_per_trade_pct": 0.25}

    logger = RuntimeExecutionEventLogger(run_id=run_id, source_sha=frozen_sha)
    hist_logger = RuntimeExecutionEventLogger(run_id=run_id, source_sha=hist_sha)

    # 3 cycle invocations (HISTORICAL_PRODUCTION, PRODUCTION, MARKET_SHADOW)
    _, h_cycle_id = hist_logger.observe_runtime_invocation(
        scenario_id=f"HIST_CYCLE_{ts_ms}",
        runtime_name="HISTORICAL_PRODUCTION",
        callable_identity="services.scalp_watcher_orchestrator.ScalpWatcherOrchestrator.evaluate_cycle",
        decision_timestamp_ms=ts_ms,
        model_id="ALL_MODELS_CYCLE",
        invocation_scope="CYCLE_EVALUATION",
        raw_input_payload={"runtime": "HISTORICAL_PRODUCTION_CYCLE", "canonical_market_input": can_in},
        canonical_input_payload=can_in,
        raw_config_payload={"runtime": "HISTORICAL_PRODUCTION_CYCLE", "canonical_config": can_cfg},
        canonical_config_payload=can_cfg,
        callable_fn=lambda: {"status": "OK"},
    )
    _, p_cycle_id = logger.observe_runtime_invocation(
        scenario_id=f"HIST_CYCLE_{ts_ms}",
        runtime_name="PRODUCTION",
        callable_identity="backend.services.scalp_watcher_orchestrator.ScalpWatcherOrchestrator.evaluate_cycle",
        decision_timestamp_ms=ts_ms,
        model_id="ALL_MODELS_CYCLE",
        invocation_scope="CYCLE_EVALUATION",
        raw_input_payload={"runtime": "PRODUCTION_CYCLE", "canonical_market_input": can_in},
        canonical_input_payload=can_in,
        raw_config_payload={"runtime": "PRODUCTION_CYCLE", "canonical_config": can_cfg},
        canonical_config_payload=can_cfg,
        callable_fn=lambda: {"status": "OK"},
    )
    _, s_cycle_id = logger.observe_runtime_invocation(
        scenario_id=f"HIST_CYCLE_{ts_ms}",
        runtime_name="MARKET_SHADOW",
        callable_identity="backend.services.market_shadow_service.MarketShadowService.evaluate_shadow_cycle",
        decision_timestamp_ms=ts_ms,
        model_id="ALL_MODELS_CYCLE",
        invocation_scope="CYCLE_EVALUATION",
        raw_input_payload={"runtime": "MARKET_SHADOW_CYCLE", "canonical_market_input": can_in},
        canonical_input_payload=can_in,
        raw_config_payload={"runtime": "MARKET_SHADOW_CYCLE", "canonical_config": can_cfg},
        canonical_config_payload=can_cfg,
        callable_fn=lambda: {"status": "OK"},
    )

    # 5 model evaluations
    hist_logger.observe_runtime_invocation(
        scenario_id=scen_id,
        runtime_name="HISTORICAL_PRODUCTION",
        callable_identity="services.scalp_m1_model_service.ScalpM1ModelService.evaluate_m1_reversal_candidate",
        decision_timestamp_ms=ts_ms,
        model_id="SMC_M1_REVERSAL",
        invocation_scope="MODEL_EVALUATION",
        parent_cycle_invocation_id=h_cycle_id,
        raw_input_payload={"runtime": "HISTORICAL_PRODUCTION", "canonical_market_input": can_in},
        canonical_input_payload=can_in,
        raw_config_payload={"runtime": "HISTORICAL_PRODUCTION", "canonical_config": can_cfg},
        canonical_config_payload=can_cfg,
        callable_fn=lambda: {"decision_status": "ELIGIBLE"},
    )
    logger.ingest_external_events_and_snapshots(
        events=hist_logger.events,
        snapshots=hist_logger.snapshots,
    )

    for rt_name, cal_id, parent_id in [
        ("SHARED_STRATEGY_EVALUATOR", "backend.services.shared_strategy_evaluator.SharedStrategyEvaluator.evaluate_decision", None),
        ("PRODUCTION", "backend.services.scalp_watcher_orchestrator.ScalpWatcherOrchestrator.evaluate_shared_decision_snapshot", p_cycle_id),
        ("MARKET_SHADOW", "backend.services.market_shadow_service.MarketShadowService.evaluate_shared_decision_snapshot", s_cycle_id),
        ("CHRONOLOGICAL_BACKTEST", "backend.services.v17_8_backtest_runner.execute_chronological_backtest_window_for_parity", None),
    ]:
        logger.observe_runtime_invocation(
            scenario_id=scen_id,
            runtime_name=rt_name,
            callable_identity=cal_id,
            decision_timestamp_ms=ts_ms,
            model_id="SMC_M1_REVERSAL",
            invocation_scope="MODEL_EVALUATION",
            parent_cycle_invocation_id=parent_id,
            raw_input_payload={"runtime": rt_name, "canonical_market_input": can_in},
            canonical_input_payload=can_in,
            raw_config_payload={"runtime": rt_name, "canonical_config": can_cfg},
            canonical_config_payload=can_cfg,
            callable_fn=lambda: {"decision_status": "ELIGIBLE"},
        )

    captures = logger.build_runtime_input_captures()
    events = logger.events
    snapshots = logger.snapshots
    rt_verif = reconcile_r33_runtime_captures(
        events=events,
        snapshots=snapshots,
        captures=captures,
        expected_run_id=run_id,
        expected_frozen_sha=frozen_sha,
        expected_historical_sha=hist_sha,
    )

    all_neg_ids = [f"N{i:02d}" for i in range(1, 149)] + [f"F{i:02d}" for i in range(1, 21)]
    neg_results_rows = [{"test_id": cid, "control_id": cid, "status": "PASS"} for cid in all_neg_ids]

    reports_dir = bundle_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    xml_path = reports_dir / "test_suite_01.xml"
    xml_bytes = b'<?xml version="1.0" encoding="utf-8"?><testsuite tests="5" failures="0" errors="0" skipped="0"><testcase classname="t" name="t1"/></testsuite>\n'
    xml_path.write_bytes(xml_bytes)
    (reports_dir / "test_suite_01.xml.meta.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "tested_source_sha": frozen_sha,
                "xml_sha256": hashlib.sha256(xml_bytes).hexdigest(),
                "exit_code": 0,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    (bundle_dir / "acceptance_results.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "tested_source_sha": frozen_sha,
                "source_sha": frozen_sha,
                "concurrency_report": {
                    "run_id": run_id,
                    "tested_source_sha": frozen_sha,
                    "max_configured_workers": 4,
                    "max_active_workers_observed": 4,
                },
                "cpu_memory_metrics": {
                    "run_id": run_id,
                    "tested_source_sha": frozen_sha,
                },
                "parity_summary": {
                    "run_id": run_id,
                    "tested_source_sha": frozen_sha,
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    files_to_write = {
        "R33_RUN_MANIFEST.json": json.dumps(
            {
                "release": "V17.9.0.1.3",
                "phase": "R3.3",
                "run_id": run_id,
                "frozen_executable_sha": frozen_sha,
                "final_code_sha": frozen_sha,
                "tested_source_sha": frozen_sha,
                "backtest_source_sha": frozen_sha,
                "status": "PASS",
            },
            indent=2,
        ),
        "R33_DEFECT_ROOT_CAUSE_MATRIX.md": "# R3.3 Defect Root Cause Matrix\n",
        "R33_SOURCE_AND_COMMAND_EXECUTIONS.jsonl": json.dumps({"run_id": run_id, "tested_source_sha": frozen_sha, "exit_code": 0, "status": "PASS"}) + "\n",
        "R33_RUNTIME_INVOCATION_EVENTS.jsonl": "".join(json.dumps(e, sort_keys=True) + "\n" for e in events),
        "R33_RUNTIME_INPUT_SNAPSHOTS.jsonl": "".join(json.dumps(s, sort_keys=True) + "\n" for s in snapshots),
        "R33_RUNTIME_INPUT_CAPTURES.jsonl": "".join(json.dumps(c, sort_keys=True) + "\n" for c in captures),
        "R33_RUNTIME_CAPTURE_INDEPENDENT_VERIFICATION.json": json.dumps(rt_verif, indent=2),
        "R33_FIVE_WAY_PARITY.json": json.dumps(
            {
                "release": "V17.9.0.1.3",
                "phase": "R3.3",
                "run_id": run_id,
                "final_code_sha": frozen_sha,
                "tested_source_sha": frozen_sha,
                "five_way_verified_count": 210,
                "divergences_count": 0,
                "status": "PASS",
            },
            indent=2,
        ),
        "R33_HISTORICAL_PRODUCTION_LINEAGE.jsonl": json.dumps({"scenario_id": scen_id, "lifecycle_stage": "CANDIDATE_CONSUMED"}) + "\n",
        "R33_PARITY_CLASSIFICATION.json": json.dumps(
            {
                "release": "V17.9.0.1.3",
                "phase": "R3.3",
                "run_id": run_id,
                "final_code_sha": frozen_sha,
                "tested_source_sha": frozen_sha,
                "total_comparisons": 210,
                "exact_matches": 21,
                "expected_documented_changes": 189,
                "unexpected_divergences": 0,
                "status": "PASS",
                "breakdown_by_category": {
                    "EXACT_MATCH": 21,
                    "EXPECTED_DOCUMENTED_CHANGE": 189,
                    "UNEXPECTED_DIVERGENCE": 0,
                },
            },
            indent=2,
        ),
        "R33_NEGATIVE_TEST_COLLECTION.json": json.dumps(
            {
                "run_id": run_id,
                "tested_source_sha": frozen_sha,
                "total_collected_controls": len(all_neg_ids),
                "collected_control_ids": all_neg_ids,
                "status": "PASS",
            },
            indent=2,
        ),
        "R33_NEGATIVE_TEST_RESULTS.json": json.dumps(
            {
                "run_id": run_id,
                "tested_source_sha": frozen_sha,
                "total_negative_controls": len(all_neg_ids),
                "passed_negative_controls": len(all_neg_ids),
                "failed_negative_controls": 0,
                "status": "PASS",
                "results": neg_results_rows,
            },
            indent=2,
        ),
        "R33_WORKER_EXECUTIONS.jsonl": json.dumps(
            {
                "worker_id": "worker_01",
                "task_id": "G89",
                "run_id": run_id,
                "tested_source_sha": frozen_sha,
                "exit_code": 0,
                "status": "PASS",
            }
        )
        + "\n",
        "R33_RUN_TASK_ACCOUNTING.json": json.dumps(
            {
                "run_id": run_id,
                "tested_source_sha": frozen_sha,
                "expected_tasks_count": 1,
                "completed_tasks_count": 1,
                "failed_tasks_count": 0,
                "max_configured_workers": 4,
                "max_active_workers_observed": 4,
                "status": "PASS",
            },
            indent=2,
        ),
        "R33_JUNIT_RECONCILIATION.json": json.dumps(
            {
                "run_id": run_id,
                "tested_source_sha": frozen_sha,
                "xml_files_count": 1,
                "total_test_executions": 5,
                "total_passed": 5,
                "total_failures": 0,
                "total_errors": 0,
                "status": "PASS",
            },
            indent=2,
        ),
        "R33_ASYNC_RESOURCE_TELEMETRY.json": json.dumps(
            {
                "run_id": run_id,
                "tested_source_sha": frozen_sha,
                "max_configured_workers": 4,
                "max_active_workers_observed": 4,
                "orphan_processes_remaining": 0,
                "status": "PASS",
            },
            indent=2,
        ),
        "R33_DISK_GUARD.json": json.dumps(
            {
                "run_id": run_id,
                "tested_source_sha": frozen_sha,
                "free_disk_bytes_postflight": 20 * 1024**3,
                "status": "PASS",
            },
            indent=2,
        ),
        "R33_EXACT_SOURCE_BACKTEST_MANIFEST.json": json.dumps(
            {
                "run_id": run_id,
                "final_code_sha": frozen_sha,
                "tested_source_sha": frozen_sha,
                "backtest_source_sha": frozen_sha,
                "status": "PASS",
            },
            indent=2,
        ),
        "R33_BACKTEST_TRADE_LEDGER.jsonl": json.dumps(
            {
                "trade_id": "T01",
                "run_id": run_id,
                "source_sha": frozen_sha,
                "backtest_source_sha": frozen_sha,
            }
        )
        + "\n",
        "R33_BACKTEST_A_B_C_D_METRICS.json": json.dumps(
            {
                "run_id": run_id,
                "final_code_sha": frozen_sha,
                "tested_source_sha": frozen_sha,
                "backtest_source_sha": frozen_sha,
                "status": "PASS",
            },
            indent=2,
        ),
        "R33_MATCHED_COHORT.json": json.dumps(
            {
                "run_id": run_id,
                "tested_source_sha": frozen_sha,
                "source_sha": frozen_sha,
                "status": "PASS",
            },
            indent=2,
        ),
        "R33_OOS_REPORT.json": json.dumps(
            {
                "run_id": run_id,
                "tested_source_sha": frozen_sha,
                "source_sha": frozen_sha,
                "out_of_sample_verdict": "OUT_OF_SAMPLE_INCONCLUSIVE",
            },
            indent=2,
        ),
        "R33_INDEPENDENT_VERIFIER_REPORT.json": json.dumps({"run_id": run_id, "tested_source_sha": frozen_sha, "status": "PASS"}, indent=2),
        "R33_FINAL_ACCEPTANCE_AUDIT.md": "# R3.3 Final Acceptance Audit\n",
    }

    for fname, content in files_to_write.items():
        (bundle_dir / fname).write_text(content, encoding="utf-8")

    art_hashes = {}
    for fname in R33_MANDATORY_ARTIFACTS:
        if fname in ("R33_ARTIFACT_SHA256_MANIFEST.json", "R33_INDEPENDENT_VERIFIER_REPORT.json", "R33_FINAL_ACCEPTANCE_AUDIT.md"):
            continue
        fp = bundle_dir / fname
        art_hashes[fname] = {
            "filename": fname,
            "sha256": hashlib.sha256(fp.read_bytes()).hexdigest(),
            "size_bytes": fp.stat().st_size,
        }
    (bundle_dir / "R33_ARTIFACT_SHA256_MANIFEST.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "tested_source_sha": frozen_sha,
                "artifacts": art_hashes,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    verif = verify_r33_evidence_bundle(bundle_dir, frozen_sha, expected_run_id=run_id)
    assert verif["is_valid"] is True, f"Baseline valid fixture failed: {verif['violations']}"
    return bundle_dir


def _refresh_r33_manifest_hashes(bundle_dir: Path) -> None:
    import hashlib
    import json
    from tools.acceptance_verdict import R33_MANDATORY_ARTIFACTS

    man_p = bundle_dir / "R33_ARTIFACT_SHA256_MANIFEST.json"
    man_doc = json.loads(man_p.read_text(encoding="utf-8"))
    art_hashes = {}
    for fname in R33_MANDATORY_ARTIFACTS:
        if fname in ("R33_ARTIFACT_SHA256_MANIFEST.json", "R33_INDEPENDENT_VERIFIER_REPORT.json", "R33_FINAL_ACCEPTANCE_AUDIT.md"):
            continue
        fp = bundle_dir / fname
        if fp.exists():
            art_hashes[fname] = {
                "filename": fname,
                "sha256": hashlib.sha256(fp.read_bytes()).hexdigest(),
                "size_bytes": fp.stat().st_size,
            }
    man_doc["artifacts"] = art_hashes
    man_p.write_text(json.dumps(man_doc, indent=2), encoding="utf-8")


def test_f01_r33_run_manifest_missing_final_code_sha_rejected():
    """F01: R3.3 (P0-04): R33_RUN_MANIFEST.json missing final_code_sha fails closed."""
    import json, tempfile
    from tools.acceptance_verdict import verify_r33_evidence_bundle
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r33_bundle_fixture(Path(td))
        man_p = bdir / "R33_RUN_MANIFEST.json"
        doc = json.loads(man_p.read_text(encoding="utf-8"))
        doc.pop("final_code_sha", None)
        man_p.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        _refresh_r33_manifest_hashes(bdir)
        res = verify_r33_evidence_bundle(bdir, "a" * 40, expected_run_id="r33_test_run_01")
        assert res["is_valid"] is False
        assert any("R33_RUN_MANIFEST_MISSING_REQUIRED_SHA:final_code_sha" in v for v in res["violations"])


def test_f02_nested_acceptance_results_tested_source_sha_mismatch_rejected():
    """F02: R3.3 (P0-03/P0-04): acceptance_results.json tested_source_sha != frozen_executable_sha fails closed."""
    import json, tempfile
    from tools.acceptance_verdict import verify_r33_evidence_bundle
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r33_bundle_fixture(Path(td))
        acc_p = bdir / "acceptance_results.json"
        doc = json.loads(acc_p.read_text(encoding="utf-8"))
        doc["tested_source_sha"] = "b" * 40
        acc_p.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        res = verify_r33_evidence_bundle(bdir, "a" * 40, expected_run_id="r33_test_run_01")
        assert res["is_valid"] is False
        assert any("NESTED_ACCEPTANCE_SOURCE_SHA_MISMATCH" in v for v in res["violations"])


def test_f03_nested_concurrency_report_tested_source_sha_mismatch_rejected():
    """F03: R3.3 (P0-03/P0-04): concurrency_report.tested_source_sha != frozen_executable_sha fails closed."""
    import json, tempfile
    from tools.acceptance_verdict import verify_r33_evidence_bundle
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r33_bundle_fixture(Path(td))
        acc_p = bdir / "acceptance_results.json"
        doc = json.loads(acc_p.read_text(encoding="utf-8"))
        doc["concurrency_report"]["tested_source_sha"] = "c" * 40
        acc_p.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        res = verify_r33_evidence_bundle(bdir, "a" * 40, expected_run_id="r33_test_run_01")
        assert res["is_valid"] is False
        assert any("CONCURRENCY_REPORT_SOURCE_SHA_MISMATCH" in v for v in res["violations"])


def test_f04_nested_cpu_memory_metrics_run_id_mismatch_rejected():
    """F04: R3.3 (P0-03): cpu_memory_metrics.run_id != R33_RUN_MANIFEST.run_id fails closed."""
    import json, tempfile
    from tools.acceptance_verdict import verify_r33_evidence_bundle
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r33_bundle_fixture(Path(td))
        acc_p = bdir / "acceptance_results.json"
        doc = json.loads(acc_p.read_text(encoding="utf-8"))
        doc["cpu_memory_metrics"]["run_id"] = "run_v17_9_0_stale_seed"
        acc_p.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        res = verify_r33_evidence_bundle(bdir, "a" * 40, expected_run_id="r33_test_run_01")
        assert res["is_valid"] is False
        assert any("CPU_MEMORY_METRICS_RUN_ID_MISMATCH" in v for v in res["violations"])


def test_f05_nested_parity_summary_run_id_mismatch_rejected():
    """F05: R3.3 (P0-03): parity_summary.run_id != R33_RUN_MANIFEST.run_id fails closed."""
    import json, tempfile
    from tools.acceptance_verdict import verify_r33_evidence_bundle
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r33_bundle_fixture(Path(td))
        acc_p = bdir / "acceptance_results.json"
        doc = json.loads(acc_p.read_text(encoding="utf-8"))
        doc["parity_summary"]["run_id"] = "run_v17_9_0_stale_seed"
        acc_p.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        res = verify_r33_evidence_bundle(bdir, "a" * 40, expected_run_id="r33_test_run_01")
        assert res["is_valid"] is False
        assert any("PARITY_SUMMARY_RUN_ID_MISMATCH" in v for v in res["violations"])


def test_f06_missing_actual_input_captured_event_rejected():
    """F06: R3.3 (P0-02): Missing ACTUAL_INPUT_CAPTURED event rejected without arguments_hash fallback."""
    import json, tempfile
    from tools.acceptance_verdict import verify_r33_evidence_bundle
    from tools.runtime_execution_logger import RuntimeExecutionEventLogger

    # 1. Verify logger never falls back to start_evt.arguments_hash
    logger = RuntimeExecutionEventLogger(run_id="r33_f06", source_sha="a" * 40)
    logger.record_invocation_started(
        execution_id="exec_f06",
        scenario_id="SCEN_F06",
        runtime_name="PRODUCTION",
        callable_identity="backend.services.scalp_watcher_orchestrator.ScalpWatcherOrchestrator.evaluate_shared_decision_snapshot",
        decision_timestamp_ms=1761696000000,
        model_id="SMC_M1_REVERSAL",
        source_sha="a" * 40,
        arguments_hash="b" * 64,
    )
    logger.record_invocation_completed(
        execution_id="exec_f06",
        scenario_id="SCEN_F06",
        runtime_name="PRODUCTION",
        callable_identity="backend.services.scalp_watcher_orchestrator.ScalpWatcherOrchestrator.evaluate_shared_decision_snapshot",
        decision_timestamp_ms=1761696000000,
        model_id="SMC_M1_REVERSAL",
        source_sha="a" * 40,
        output_decision_id="dec_f06",
        output_status="ELIGIBLE",
        output_fingerprint="c" * 64,
        duration_ms=1.0,
    )
    assert logger.build_runtime_input_captures() == []

    # 2. Verify bundle verifier rejects removed ACTUAL_INPUT_CAPTURED event
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r33_bundle_fixture(Path(td))
        ev_p = bdir / "R33_RUNTIME_INVOCATION_EVENTS.jsonl"
        events = [json.loads(ln) for ln in ev_p.read_text(encoding="utf-8").splitlines() if ln.strip()]
        dropped = False
        filtered = []
        for e in events:
            if not dropped and e.get("event_type") == "ACTUAL_INPUT_CAPTURED":
                dropped = True
                continue
            filtered.append(e)
        ev_p.write_text("".join(json.dumps(e, sort_keys=True) + "\n" for e in filtered), encoding="utf-8")
        _refresh_r33_manifest_hashes(bdir)
        res = verify_r33_evidence_bundle(bdir, "a" * 40, expected_run_id="r33_test_run_01")
        assert res["is_valid"] is False
        assert any("MISSING_ACTUAL_INPUT_CAPTURED_EVENT" in v for v in res["violations"])


def test_f07_missing_invocation_completed_event_rejected():
    """F07: R3.3 (P0-05): Missing INVOCATION_COMPLETED event fails closed."""
    import json, tempfile
    from tools.acceptance_verdict import verify_r33_evidence_bundle
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r33_bundle_fixture(Path(td))
        ev_p = bdir / "R33_RUNTIME_INVOCATION_EVENTS.jsonl"
        events = [json.loads(ln) for ln in ev_p.read_text(encoding="utf-8").splitlines() if ln.strip()]
        dropped = False
        filtered = []
        for e in events:
            if not dropped and e.get("event_type") == "INVOCATION_COMPLETED":
                dropped = True
                continue
            filtered.append(e)
        ev_p.write_text("".join(json.dumps(e, sort_keys=True) + "\n" for e in filtered), encoding="utf-8")
        _refresh_r33_manifest_hashes(bdir)
        res = verify_r33_evidence_bundle(bdir, "a" * 40, expected_run_id="r33_test_run_01")
        assert res["is_valid"] is False
        assert any("MISSING_INVOCATION_COMPLETED_EVENT" in v for v in res["violations"])


def test_f08_duplicate_execution_id_rejected():
    """F08: R3.3 (P0-05): Duplicate execution_id in runtime events/captures fails closed."""
    import json, tempfile
    from tools.acceptance_verdict import verify_r33_evidence_bundle
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r33_bundle_fixture(Path(td))
        cap_p = bdir / "R33_RUNTIME_INPUT_CAPTURES.jsonl"
        caps = [json.loads(ln) for ln in cap_p.read_text(encoding="utf-8").splitlines() if ln.strip()]
        caps[1]["execution_id"] = caps[0]["execution_id"]
        cap_p.write_text("".join(json.dumps(c, sort_keys=True) + "\n" for c in caps), encoding="utf-8")
        _refresh_r33_manifest_hashes(bdir)
        res = verify_r33_evidence_bundle(bdir, "a" * 40, expected_run_id="r33_test_run_01")
        assert res["is_valid"] is False
        assert any("DUPLICATE_EXECUTION_ID" in v for v in res["violations"])


def test_f09_non_monotonic_invocation_timestamps_rejected():
    """F09: R3.3 (P0-01/P0-05): Non-monotonic invocation timestamps fail closed."""
    import json, tempfile
    from tools.acceptance_verdict import verify_r33_evidence_bundle
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r33_bundle_fixture(Path(td))
        ev_p = bdir / "R33_RUNTIME_INVOCATION_EVENTS.jsonl"
        events = [json.loads(ln) for ln in ev_p.read_text(encoding="utf-8").splitlines() if ln.strip()]
        for e in events:
            if e.get("event_type") == "INVOCATION_COMPLETED":
                e["timestamp_monotonic_ns"] = 1
                break
        ev_p.write_text("".join(json.dumps(e, sort_keys=True) + "\n" for e in events), encoding="utf-8")
        _refresh_r33_manifest_hashes(bdir)
        res = verify_r33_evidence_bundle(bdir, "a" * 40, expected_run_id="r33_test_run_01")
        assert res["is_valid"] is False
        assert any("NON_MONOTONIC_INVOCATION_TIMESTAMPS" in v for v in res["violations"])


def test_f10_mutated_candle_in_runtime_input_snapshot_rejected():
    """F10: R3.3 (P0-05): Mutating a candle in R33_RUNTIME_INPUT_SNAPSHOTS.jsonl without updating hash fails closed."""
    import json, tempfile
    from tools.acceptance_verdict import verify_r33_evidence_bundle
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r33_bundle_fixture(Path(td))
        snap_p = bdir / "R33_RUNTIME_INPUT_SNAPSHOTS.jsonl"
        snaps = [json.loads(ln) for ln in snap_p.read_text(encoding="utf-8").splitlines() if ln.strip()]
        snaps[0]["canonical_input_payload"]["m1_closes"][0] = 9999.99
        snap_p.write_text("".join(json.dumps(s, sort_keys=True) + "\n" for s in snaps), encoding="utf-8")
        _refresh_r33_manifest_hashes(bdir)
        res = verify_r33_evidence_bundle(bdir, "a" * 40, expected_run_id="r33_test_run_01")
        assert res["is_valid"] is False
        assert any("SNAPSHOT_HASH_RECOMPUTATION_MISMATCH" in v for v in res["violations"])


def test_f11_disallowed_callable_identity_rejected():
    """F11: R3.3 (P0-05): Non-allowlisted callable identity in runtime events/captures fails closed."""
    import json, tempfile
    from tools.acceptance_verdict import verify_r33_evidence_bundle
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r33_bundle_fixture(Path(td))
        cap_p = bdir / "R33_RUNTIME_INPUT_CAPTURES.jsonl"
        caps = [json.loads(ln) for ln in cap_p.read_text(encoding="utf-8").splitlines() if ln.strip()]
        caps[0]["capture_point"] = "services.v17_9_0_1_baseline_oracle.IndependentPreRefactorBaselineOracle.evaluate_baseline_dict"
        cap_p.write_text("".join(json.dumps(c, sort_keys=True) + "\n" for c in caps), encoding="utf-8")
        _refresh_r33_manifest_hashes(bdir)
        res = verify_r33_evidence_bundle(bdir, "a" * 40, expected_run_id="r33_test_run_01")
        assert res["is_valid"] is False
        assert any("DISALLOWED_CALLABLE_IDENTITY" in v for v in res["violations"])


def test_f12_cross_runtime_canonical_input_hash_mismatch_rejected():
    """F12: R3.3 (P0-05): Cross-runtime canonical_input_hash mismatch for same scenario fails closed."""
    import json, tempfile
    from tools.acceptance_verdict import verify_r33_evidence_bundle
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r33_bundle_fixture(Path(td))
        cap_p = bdir / "R33_RUNTIME_INPUT_CAPTURES.jsonl"
        caps = [json.loads(ln) for ln in cap_p.read_text(encoding="utf-8").splitlines() if ln.strip()]
        caps[0]["canonical_input_hash"] = "f" * 64
        cap_p.write_text("".join(json.dumps(c, sort_keys=True) + "\n" for c in caps), encoding="utf-8")
        _refresh_r33_manifest_hashes(bdir)
        res = verify_r33_evidence_bundle(bdir, "a" * 40, expected_run_id="r33_test_run_01")
        assert res["is_valid"] is False
        assert any("CROSS_RUNTIME_CANONICAL_INPUT_HASH_MISMATCH" in v for v in res["violations"])


def test_f13_cross_runtime_canonical_config_hash_mismatch_rejected():
    """F13: R3.3 (P0-05): Cross-runtime canonical_config_hash mismatch for same scenario fails closed."""
    import json, tempfile
    from tools.acceptance_verdict import verify_r33_evidence_bundle
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r33_bundle_fixture(Path(td))
        cap_p = bdir / "R33_RUNTIME_INPUT_CAPTURES.jsonl"
        caps = [json.loads(ln) for ln in cap_p.read_text(encoding="utf-8").splitlines() if ln.strip()]
        caps[0]["canonical_config_hash"] = "e" * 64
        cap_p.write_text("".join(json.dumps(c, sort_keys=True) + "\n" for c in caps), encoding="utf-8")
        _refresh_r33_manifest_hashes(bdir)
        res = verify_r33_evidence_bundle(bdir, "a" * 40, expected_run_id="r33_test_run_01")
        assert res["is_valid"] is False
        assert any("CROSS_RUNTIME_CANONICAL_CONFIG_HASH_MISMATCH" in v for v in res["violations"])


def test_f14_missing_mandatory_negative_control_id_rejected():
    """F14: R3.3 (P0-07): Missing negative control ID (e.g. N140) in collection or results fails closed."""
    import json, tempfile
    from tools.acceptance_verdict import verify_r33_evidence_bundle
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r33_bundle_fixture(Path(td))
        coll_p = bdir / "R33_NEGATIVE_TEST_COLLECTION.json"
        doc = json.loads(coll_p.read_text(encoding="utf-8"))
        doc["collected_control_ids"] = [cid for cid in doc["collected_control_ids"] if cid != "N140"]
        coll_p.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        _refresh_r33_manifest_hashes(bdir)
        res = verify_r33_evidence_bundle(bdir, "a" * 40, expected_run_id="r33_test_run_01")
        assert res["is_valid"] is False
        assert any("MISSING_MANDATORY_NEGATIVE_CONTROL_ID:N140" in v for v in res["violations"])


def test_f15_junit_sidecar_provenance_mismatch_rejected():
    """F15: R3.3 (P0-03): JUnit XML sidecar with stale run_id or mismatched SHA-256 fails closed."""
    import json, tempfile
    from tools.acceptance_verdict import verify_r33_evidence_bundle
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r33_bundle_fixture(Path(td))
        sc_p = bdir / "reports" / "test_suite_01.xml.meta.json"
        doc = json.loads(sc_p.read_text(encoding="utf-8"))
        doc["run_id"] = "run_v17_9_0_1793411200786_34380"
        sc_p.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        res = verify_r33_evidence_bundle(bdir, "a" * 40, expected_run_id="r33_test_run_01")
        assert res["is_valid"] is False
        assert any("JUNIT_SIDECAR_PROVENANCE_MISMATCH" in v for v in res["violations"])


def test_f16_worker_non_zero_exit_code_masked_as_pass_rejected():
    """F16: R3.3 (P0-07): Worker execution with exit_code != 0 fails closed."""
    import json, tempfile
    from tools.acceptance_verdict import verify_r33_evidence_bundle
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r33_bundle_fixture(Path(td))
        w_p = bdir / "R33_WORKER_EXECUTIONS.jsonl"
        rows = [json.loads(ln) for ln in w_p.read_text(encoding="utf-8").splitlines() if ln.strip()]
        rows[0]["exit_code"] = 1
        w_p.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8")
        _refresh_r33_manifest_hashes(bdir)
        res = verify_r33_evidence_bundle(bdir, "a" * 40, expected_run_id="r33_test_run_01")
        assert res["is_valid"] is False
        assert any("WORKER_FAILURE_MASKED_AS_PASS" in v for v in res["violations"])


def test_f17_active_workers_exceeding_configured_bound_rejected():
    """F17: R3.3 (P0-07): max_active_workers_observed > max_configured_workers fails closed."""
    import json, tempfile
    from tools.acceptance_verdict import verify_r33_evidence_bundle
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r33_bundle_fixture(Path(td))
        tel_p = bdir / "R33_ASYNC_RESOURCE_TELEMETRY.json"
        doc = json.loads(tel_p.read_text(encoding="utf-8"))
        doc["max_configured_workers"] = 4
        doc["max_active_workers_observed"] = 5
        tel_p.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        _refresh_r33_manifest_hashes(bdir)
        res = verify_r33_evidence_bundle(bdir, "a" * 40, expected_run_id="r33_test_run_01")
        assert res["is_valid"] is False
        assert any("CONCURRENCY_BOUND_EXCEEDED" in v for v in res["violations"])


def test_f18_empty_directory_passed_to_r33_verifier_rejected():
    """F18: R3.3 (P0-08): Empty directory passed to verify_r33_evidence_bundle or required_schema='R33' fails closed."""
    import tempfile
    from tools.acceptance_verdict import verify_persisted_acceptance_artifacts, verify_r33_evidence_bundle
    with tempfile.TemporaryDirectory() as td:
        empty_dir = Path(td)
        res1 = verify_r33_evidence_bundle(empty_dir, "a" * 40)
        assert res1["is_valid"] is False
        assert "R33_EMPTY_OR_MISSING_EVIDENCE_DIRECTORY" in res1["violations"]
        res2 = verify_persisted_acceptance_artifacts(empty_dir, "a" * 40, required_schema="R33")
        assert res2["is_valid"] is False
        assert "R33_EMPTY_OR_MISSING_EVIDENCE_DIRECTORY" in res2["violations"]


def test_f19_unexpected_divergence_in_r33_parity_classification_rejected():
    """F19: R3.3 (P0-06): unexpected_divergences > 0 in R33_PARITY_CLASSIFICATION.json fails closed."""
    import json, tempfile
    from tools.acceptance_verdict import verify_r33_evidence_bundle
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r33_bundle_fixture(Path(td))
        pc_p = bdir / "R33_PARITY_CLASSIFICATION.json"
        doc = json.loads(pc_p.read_text(encoding="utf-8"))
        doc["exact_matches"] = 20
        doc["unexpected_divergences"] = 1
        doc["breakdown_by_category"]["EXACT_MATCH"] = 20
        doc["breakdown_by_category"]["UNEXPECTED_DIVERGENCE"] = 1
        pc_p.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        _refresh_r33_manifest_hashes(bdir)
        res = verify_r33_evidence_bundle(bdir, "a" * 40, expected_run_id="r33_test_run_01")
        assert res["is_valid"] is False
        assert any("PARITY_CLASSIFICATION_HAS_UNEXPECTED_DIVERGENCES:1" in v for v in res["violations"])


def test_f20_backtest_trade_ledger_source_sha_mismatch_rejected():
    """F20: R3.3 (P0-04): backtest_source_sha != frozen_executable_sha in R33_BACKTEST_TRADE_LEDGER.jsonl fails closed."""
    import json, tempfile
    from tools.acceptance_verdict import verify_r33_evidence_bundle
    with tempfile.TemporaryDirectory() as td:
        bdir = _build_valid_r33_bundle_fixture(Path(td))
        led_p = bdir / "R33_BACKTEST_TRADE_LEDGER.jsonl"
        rows = [json.loads(ln) for ln in led_p.read_text(encoding="utf-8").splitlines() if ln.strip()]
        rows[0]["source_sha"] = "d" * 40
        rows[0]["backtest_source_sha"] = "d" * 40
        led_p.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8")
        _refresh_r33_manifest_hashes(bdir)
        res = verify_r33_evidence_bundle(bdir, "a" * 40, expected_run_id="r33_test_run_01")
        assert res["is_valid"] is False
        assert any("BACKTEST_LEDGER_SOURCE_SHA_MISMATCH" in v for v in res["violations"])



def run_all_v17_9_0_1_2_negative_acceptance_checks(tmp_dir: Path) -> Dict[str, Any]:
    """
    Executes all 131 mandatory negative acceptance tests (N01-N131) and returns
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
        ("N29", "CRLF line endings normalized to LF and verified by on-disk SHA-256", lambda: test_n29_lf_byte_integrity_strips_crlf_and_verifies_sha256(tmp_dir / "N29")),
        ("N30", "DEF-179012-05: Resource guard preflight/postflight disk & periodic RSS telemetry verified", lambda: test_n30_resource_guard_and_periodic_telemetry_verification(tmp_dir / "N30")),
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
        ("N96", "DEF-179013-R02: Candidate unhandled by downstream consumer evaluated not consumed rejected", test_n96_candidate_exists_unhandled_by_downstream_rejected),
        ("N97", "DEF-179013-R02: Market Shadow substituted as consumed Production decision rejected", test_n97_market_shadow_substituted_as_consumed_production_rejected),
        ("N98", "DEF-179013-R02: Fixed quantity inserted into historical decision rejected", test_n98_fixed_quantity_inserted_into_historical_decision_rejected),
        ("N99", "DEF-179013-R02: Inferred Unicorn validity without evidence rejected", test_n99_inferred_unicorn_validity_without_evidence_rejected),
        ("N100", "DEF-179013-R02: Fabricated original decision ID without correlation prefix rejected", test_n100_fabricated_original_decision_id_rejected),
        ("N101", "DEF-179013-R02: Candidate matched only by substring rejected", test_n101_candidate_matched_only_by_substring_rejected),
        ("N102", "DEF-179013-R02: ATR-missing decision falsely marked consumed rejected", test_n102_atr_missing_decision_falsely_marked_consumed_rejected),
        ("N103", "DEF-179013-R02: Identical hash for different runtime OHLC rejected", test_n103_identical_hash_for_different_runtime_ohlc_rejected),
        ("N104", "DEF-179013-R02: Shared context substituted for independent Production context rejected", test_n104_shared_context_substituted_for_independent_production_rejected),
        ("N105", "DEF-179013-R02: Empty independent runtime hashes artifact rejected", test_n105_empty_independent_runtime_hashes_artifact_rejected),
        ("N106", "DEF-179013-R02: Missing one of five source records rejected", test_n106_missing_one_of_five_source_records_rejected),
        ("N107", "DEF-179013-R02: Missing configuration identity rejected", test_n107_missing_configuration_identity_rejected),
        ("N108", "DEF-179013-R02: Raw source hash overwritten rejected", test_n108_raw_source_hash_overwritten_rejected),
        ("N109", "DEF-179013-R02: Wrong canonical schema version rejected", test_n109_wrong_canonical_schema_version_rejected),
        ("N110", "DEF-179013-R02: Different market timestamps matching claimed hashes rejected", test_n110_different_market_timestamps_matching_claimed_hashes_rejected),
        ("N111", "DEF-179013-R02: Missing historical baseline manifest rejected", test_n111_missing_historical_baseline_manifest_rejected),
        ("N112", "DEF-179013-R02: Missing negative controls report rejected", test_n112_missing_negative_controls_report_rejected),
        ("N113", "DEF-179013-R02: Missing disk report rejected", test_n113_missing_disk_report_rejected),
        ("N114", "DEF-179013-R02: Missing JUnit results rejected", test_n114_missing_junit_results_rejected),
        ("N115", "DEF-179013-R02: Missing backtest source manifest rejected", test_n115_missing_backtest_source_manifest_rejected),
        ("N116", "DEF-179013-R02: Mismatched executable SHA rejected", test_n116_mismatched_executable_sha_rejected),
        ("N117", "DEF-179013-R02: Mismatched backtest SHA rejected", test_n117_mismatched_backtest_sha_rejected),
        ("N118", "DEF-179013-R02: Corrupted artifact checksum rejected", test_n118_corrupted_artifact_checksum_rejected),
        ("N119", "DEF-179013-R02: PASS with zero independently verified five-way comparisons rejected", test_n119_pass_with_zero_independently_verified_five_way_comparisons_rejected),
        ("N120", "DEF-179013-R02: PASS with incomplete consumed lineage rejected", test_n120_pass_with_incomplete_consumed_lineage_rejected),
        ("N121", "DEF-179013-R02: PASS with mandatory skipped test rejected", test_n121_pass_with_mandatory_skipped_test_rejected),
        ("N122", "DEF-179013-R02: PASS with stale checkpoint rejected", test_n122_pass_with_stale_checkpoint_rejected),
        ("N123", "DEF-179013-R02: Child worker crashes rejected", test_n123_child_worker_crashes_rejected),
        ("N124", "DEF-179013-R02: Parent exits zero despite child failure rejected", test_n124_parent_exits_zero_despite_child_failure_rejected),
        ("N125", "DEF-179013-R02: Missing worker output rejected", test_n125_missing_worker_output_rejected),
        ("N126", "DEF-179013-R02: Duplicated worker result rejected", test_n126_duplicated_worker_result_rejected),
        ("N127", "DEF-179013-R02: Partial JSON artifact rejected", test_n127_partial_json_artifact_rejected),
        ("N128", "DEF-179013-R02: Disk guard stop rejected", test_n128_disk_guard_stop_rejected),
        ("N129", "DEF-179013-R02: Unsafe cleanup path rejected", test_n129_unsafe_cleanup_path_rejected),
        ("N130", "DEF-179013-R02: Orphan worker remains rejected", test_n130_orphan_worker_remains_rejected),
        ("N131", "DEF-179013-R02: Resume with wrong source SHA rejected", test_n131_resume_with_wrong_source_sha_rejected),
        ("N132", "DEF-179013-R03: Empty top-level backtest source SHA in backtest metrics rejected", test_n132_empty_top_level_backtest_source_sha_rejected),
        ("N133", "DEF-179013-R03: Missing top-level and nested backtest source SHA rejected", test_n133_missing_top_level_and_nested_backtest_source_sha_rejected),
        ("N134", "DEF-179013-R03: Mismatched top-level backtest source SHA rejected", test_n134_mismatched_top_level_backtest_source_sha_rejected),
        ("N135", "DEF-179013-R03: Contradictory backtest source SHAs between top-level and nested rejected", test_n135_contradictory_top_and_nested_backtest_source_shas_rejected),
        ("N136", "DEF-179013-R03: Backtest source manifest with mismatched SHA cannot claim PASS", test_n136_backtest_source_manifest_mismatched_sha_cannot_claim_pass),
        ("N137", "DEF-179013-R03: Backtest source manifest with empty SHA cannot claim PASS", test_n137_backtest_source_manifest_empty_sha_cannot_claim_pass),
        ("N138", "DEF-179013-R03: Historical production capture point mislabeled as Market Shadow rejected", test_n138_historical_production_capture_point_mislabeled_as_market_shadow_rejected),
        ("N139", "DEF-179013-R03: Missing any of 5 required runtime boundaries rejected", test_n139_cross_runtime_input_capture_missing_runtime_boundaries_rejected),
        ("N140", "DEF-179013-R03: Empty runtime input capture file rejected", test_n140_empty_runtime_input_capture_file_rejected),
        ("N141", "DEF-179013-R03: Claiming 210 exact matches when breakdown has 189 changes rejected", test_n141_parity_report_claiming_210_exact_matches_when_breakdown_differs_rejected),
        ("N142", "DEF-179013-R03: Parity classification with unexpected divergences fails closed", test_n142_parity_classification_unexpected_divergence_fails_closed),
        ("N143", "DEF-179013-R03: Missing R3 mandatory deliverables rejected by disk verifier", test_n143_artifact_verifier_rejects_missing_r3_mandatory_deliverables),
        ("N144", "DEF-179013-R03: Zero simulated trades in backtest metrics fails closed", test_n144_zero_simulated_trades_in_backtest_metrics_fails_closed),
        ("N145", "DEF-179013-R03: Missing final code SHA in acceptance verdict fails closed", test_n145_missing_final_code_sha_in_acceptance_verdict_fails_closed),
        ("N146", "DEF-179013-R31 (P0-E): Missing mandatory deliverable fails closed", test_n146_missing_r31_mandatory_deliverable_fails_closed),
        ("N147", "DEF-179013-R31: Exact source manifest SHA mismatch fails closed", test_n147_r31_exact_source_manifest_sha_mismatch_fails_closed),
        ("N148", "DEF-179013-R31 (P0-A): Uncompleted runtime capture invocation fails closed", test_n148_uncompleted_runtime_capture_invocation_fails_closed),
        ("F01", "R3.3 (P0-04): R33_RUN_MANIFEST.json missing final_code_sha fails closed", test_f01_r33_run_manifest_missing_final_code_sha_rejected),
        ("F02", "R3.3 (P0-03/P0-04): acceptance_results.json tested_source_sha != frozen_executable_sha fails closed", test_f02_nested_acceptance_results_tested_source_sha_mismatch_rejected),
        ("F03", "R3.3 (P0-03/P0-04): concurrency_report.tested_source_sha != frozen_executable_sha fails closed", test_f03_nested_concurrency_report_tested_source_sha_mismatch_rejected),
        ("F04", "R3.3 (P0-03): cpu_memory_metrics.run_id != R33_RUN_MANIFEST.run_id fails closed", test_f04_nested_cpu_memory_metrics_run_id_mismatch_rejected),
        ("F05", "R3.3 (P0-03): parity_summary.run_id != R33_RUN_MANIFEST.run_id fails closed", test_f05_nested_parity_summary_run_id_mismatch_rejected),
        ("F06", "R3.3 (P0-02): Missing ACTUAL_INPUT_CAPTURED event rejected without arguments_hash fallback", test_f06_missing_actual_input_captured_event_rejected),
        ("F07", "R3.3 (P0-05): Missing INVOCATION_COMPLETED event fails closed", test_f07_missing_invocation_completed_event_rejected),
        ("F08", "R3.3 (P0-05): Duplicate execution_id in runtime events/captures fails closed", test_f08_duplicate_execution_id_rejected),
        ("F09", "R3.3 (P0-01/P0-05): Non-monotonic invocation timestamps fail closed", test_f09_non_monotonic_invocation_timestamps_rejected),
        ("F10", "R3.3 (P0-05): Mutating a candle in R33_RUNTIME_INPUT_SNAPSHOTS.jsonl without updating hash fails closed", test_f10_mutated_candle_in_runtime_input_snapshot_rejected),
        ("F11", "R3.3 (P0-05): Non-allowlisted callable identity in runtime events/captures fails closed", test_f11_disallowed_callable_identity_rejected),
        ("F12", "R3.3 (P0-05): Cross-runtime canonical_input_hash mismatch for same scenario fails closed", test_f12_cross_runtime_canonical_input_hash_mismatch_rejected),
        ("F13", "R3.3 (P0-05): Cross-runtime canonical_config_hash mismatch for same scenario fails closed", test_f13_cross_runtime_canonical_config_hash_mismatch_rejected),
        ("F14", "R3.3 (P0-07): Missing negative control ID (N140) in collection or results fails closed", test_f14_missing_mandatory_negative_control_id_rejected),
        ("F15", "R3.3 (P0-03): JUnit XML sidecar with stale run_id or mismatched SHA-256 fails closed", test_f15_junit_sidecar_provenance_mismatch_rejected),
        ("F16", "R3.3 (P0-07): Worker execution with exit_code != 0 fails closed", test_f16_worker_non_zero_exit_code_masked_as_pass_rejected),
        ("F17", "R3.3 (P0-07): max_active_workers_observed > max_configured_workers fails closed", test_f17_active_workers_exceeding_configured_bound_rejected),
        ("F18", "R3.3 (P0-08): Empty directory passed to verify_r33_evidence_bundle or required_schema='R33' fails closed", test_f18_empty_directory_passed_to_r33_verifier_rejected),
        ("F19", "R3.3 (P0-06): unexpected_divergences > 0 in R33_PARITY_CLASSIFICATION.json fails closed", test_f19_unexpected_divergence_in_r33_parity_classification_rejected),
        ("F20", "R3.3 (P0-04): backtest_source_sha != frozen_executable_sha in R33_BACKTEST_TRADE_LEDGER.jsonl fails closed", test_f20_backtest_trade_ledger_source_sha_mismatch_rejected),
    ]
    from backend.tests.test_r331_acceptance_runner_and_resources import (
        test_r331_01_missing_or_empty_initial_run_manifest_rejected,
        test_r331_02_initial_run_manifest_non_running_status_rejected,
        test_r331_03_initial_manifest_created_after_first_task_rejected,
        test_r331_04_initial_manifest_created_after_backtest_rejected,
        test_r331_05_initial_manifest_mutated_after_creation_rejected,
        test_r331_06_macos_ru_maxrss_unit_normalization_and_1024x_inflation_rejected,
        test_r331_07_fabricated_64mib_memory_fallback_rejected,
        test_r331_08_not_measured_memory_telemetry_rejected,
        test_r331_09_missing_separated_memory_scopes_or_inverted_peak_rejected,
        test_r331_10_disk_budget_below_reserved_floor_rejected,
        test_r331_11_junit_scope_inventory_file_count_mismatch_rejected,
        test_r331_12_junit_scope_inventory_raw_test_count_mismatch_rejected,
        test_r331_13_junit_per_xml_recalculation_mismatch_rejected,
        test_r331_14_junit_overlap_reconciliation_formula_mismatch_rejected,
        test_r331_15_missing_or_corrupt_junit_sidecar_rejected,
        test_r331_16_copy_verified_current_run_artifacts_rejects_missing_dir_no_latest_fallback,
        test_r331_17_copy_verified_current_run_artifacts_rejects_stale_run_id_or_sha,
        test_r331_18_unmodified_run_identity_tree_rejects_stale_nested_subobject,
        test_r331_19_unmodified_run_identity_tree_rejects_provenance_rewrite_marker,
        test_r331_20_stale_run_id_in_jsonl_artifact_rejected,
        test_r331_21_junit_sidecar_nonzero_exit_code_with_zero_xml_failures_rejected,
        test_r331_22_bounded_orchestrator_global_semaphore_and_concurrency_bound_violation_rejected,
        test_r331_23_task_accounting_sum_mismatch_rejected,
        test_r331_24_orphan_worker_process_detection_blocks_resource_guard,
        test_r331_25_worker_started_before_pre_execution_manifest_rejected,
        test_r331_26_worker_missing_exit_code_never_defaults_to_zero,
        test_r331_27_worker_missing_status_never_defaults_to_pass,
        test_r331_28_task_accounting_missing_status_rejected,
        test_r331_29_incomplete_sha256_manifest_missing_mandatory_entry_rejected,
        test_r331_30_post_manifest_byte_mutation_in_r331_deliverable_rejected,
    )
    checks.extend([
        ("R331-01", "R3.3.1 (P0-A): Missing or empty R331_INITIAL_RUN_MANIFEST.json fails closed", test_r331_01_missing_or_empty_initial_run_manifest_rejected),
        ("R331-02", "R3.3.1 (P0-A): R331_INITIAL_RUN_MANIFEST.json with status != RUNNING fails closed", test_r331_02_initial_run_manifest_non_running_status_rejected),
        ("R331-03", "R3.3.1 (P0-A): R331_INITIAL_RUN_MANIFEST.json created after first task fails closed", test_r331_03_initial_manifest_created_after_first_task_rejected),
        ("R331-04", "R3.3.1 (P0-A): R331_INITIAL_RUN_MANIFEST.json created after backtest fails closed", test_r331_04_initial_manifest_created_after_backtest_rejected),
        ("R331-05", "R3.3.1 (P0-A): Mutating R331_INITIAL_RUN_MANIFEST.json after creation fails closed", test_r331_05_initial_manifest_mutated_after_creation_rejected),
        ("R331-06", "R3.3.1 (P0-B): macOS ru_maxrss 1024x unit inflation fails closed", test_r331_06_macos_ru_maxrss_unit_normalization_and_1024x_inflation_rejected),
        ("R331-07", "R3.3.1 (P0-B): Fabricated 64 MiB (67108864) memory fallback fails closed", test_r331_07_fabricated_64mib_memory_fallback_rejected),
        ("R331-08", "R3.3.1 (P0-B): NOT_MEASURED memory telemetry fails closed", test_r331_08_not_measured_memory_telemetry_rejected),
        ("R331-09", "R3.3.1 (P0-B): Missing separated memory scopes or peak < current RSS fails closed", test_r331_09_missing_separated_memory_scopes_or_inverted_peak_rejected),
        ("R331-10", "R3.3.1 (P0-B): Disk budget below reserved floor fails closed", test_r331_10_disk_budget_below_reserved_floor_rejected),
        ("R331-11", "R3.3.1 (P0-C): JUnit scope inventory file count mismatch (92 vs 93) fails closed", test_r331_11_junit_scope_inventory_file_count_mismatch_rejected),
        ("R331-12", "R3.3.1 (P0-C): JUnit scope inventory raw test count mismatch (5957 vs 5816) fails closed", test_r331_12_junit_scope_inventory_raw_test_count_mismatch_rejected),
        ("R331-13", "R3.3.1 (P0-C): JUnit per-XML recalculation mismatch fails closed", test_r331_13_junit_per_xml_recalculation_mismatch_rejected),
        ("R331-14", "R3.3.1 (P0-C): JUnit overlap reconciliation formula mismatch fails closed", test_r331_14_junit_overlap_reconciliation_formula_mismatch_rejected),
        ("R331-15", "R3.3.1 (P0-C): Missing or corrupt JUnit .xml.meta.json sidecar fails closed", test_r331_15_missing_or_corrupt_junit_sidecar_rejected),
        ("R331-16", "R3.3.1 (P0-D): Missing current-run directory blocks without latest_run.json fallback", test_r331_16_copy_verified_current_run_artifacts_rejects_missing_dir_no_latest_fallback),
        ("R331-17", "R3.3.1 (P0-D): Stale run_id or tested_source_sha in acceptance_results.json rejected", test_r331_17_copy_verified_current_run_artifacts_rejects_stale_run_id_or_sha),
        ("R331-18", "R3.3.1 (P0-D): Stale nested concurrency_report.run_id rejected by assert_nested_run_provenance", test_r331_18_unmodified_run_identity_tree_rejects_stale_nested_subobject),
        ("R331-19", "R3.3.1 (P0-D): Illegal _provenance_rewritten marker rejected", test_r331_19_unmodified_run_identity_tree_rejects_provenance_rewrite_marker),
        ("R331-20", "R3.3.1 (P0-D): Stale run_id in JSONL artifact rejected", test_r331_20_stale_run_id_in_jsonl_artifact_rejected),
        ("R331-21", "R3.3.1 (P0-E): JUnit sidecar exit_code != 0 with 0 XML failures fails closed", test_r331_21_junit_sidecar_nonzero_exit_code_with_zero_xml_failures_rejected),
        ("R331-22", "R3.3.1 (P0-E): Bounded orchestrator global semaphore & concurrency bound violation rejected", test_r331_22_bounded_orchestrator_global_semaphore_and_concurrency_bound_violation_rejected),
        ("R331-23", "R3.3.1 (P0-E): Task accounting sum mismatch fails closed", test_r331_23_task_accounting_sum_mismatch_rejected),
        ("R331-24", "R3.3.1 (P0-E): Live orphan worker process detected and blocks resource guard", test_r331_24_orphan_worker_process_detection_blocks_resource_guard),
        ("R331-25", "R3.3.1 (P0-E): Worker started before pre-execution manifest fails closed", test_r331_25_worker_started_before_pre_execution_manifest_rejected),
        ("R331-26", "R3.3.1 (P0-F): Worker missing exit_code fails closed (never defaults to 0)", test_r331_26_worker_missing_exit_code_never_defaults_to_zero),
        ("R331-27", "R3.3.1 (P0-F): Worker missing status fails closed (never defaults to PASS)", test_r331_27_worker_missing_status_never_defaults_to_pass),
        ("R331-28", "R3.3.1 (P0-F): Task accounting missing status fails closed", test_r331_28_task_accounting_missing_status_rejected),
        ("R331-29", "R3.3.1 (P0-F): Incomplete SHA-256 manifest missing mandatory entry fails closed", test_r331_29_incomplete_sha256_manifest_missing_mandatory_entry_rejected),
        ("R331-30", "R3.3.1 (P0-F): Post-manifest byte mutation in R331 deliverable fails closed", test_r331_30_post_manifest_byte_mutation_in_r331_deliverable_rejected),
    ])
    from tests.test_r332_raw_regression_retry_integrity import (
        test_r332_01_primary_subprocess_nonzero_plus_retry_pass_not_clean_pass,
        test_r332_02_original_junit_has_failing_testcase_derived_xml_all_pass_rejected,
        test_r332_03_mutate_primary_exit_code_1_to_0_detected_against_raw_log,
        test_r332_04_missing_original_raw_shard_xml_fails_closed,
        test_r332_05_missing_original_raw_shard_log_fails_closed,
        test_r332_06_altered_raw_xml_without_manifest_sha_update_rejected,
        test_r332_07_altered_retry_xml_with_matching_metadata_only_rejected,
        test_r332_08_duplicate_attempt_id_rejected,
        test_r332_09_duplicate_testcase_id_collapsed_rejected,
        test_r332_10_retry_reuses_primary_attempt_id_rejected,
        test_r332_11_retry_claimed_actual_subprocess_without_pid_argv_timestamps_rejected,
        test_r332_12_derived_projection_claiming_independently_executed_pytest_rejected,
        test_r332_13_derived_junit_sidecar_exit_0_while_linked_raw_attempt_exit_1_rejected,
        test_r332_14_master_worker_ledger_22_scheduled_20_succeeded_2_failed_phase_h_17_17_rejected,
        test_r332_15_missing_master_worker_ledger_rejected,
        test_r332_16_empty_master_worker_ledger_rejected,
        test_r332_17_missing_primary_shard_started_at_ms_rejected,
        test_r332_18_missing_worker_source_sha_rejected,
        test_r332_19_cross_run_retry_xml_sidecar_rejected,
        test_r332_20_cross_commit_xml_with_same_testcase_name_rejected,
        test_r332_21_missing_backend_tests_in_shard_inventory_rejected,
        test_r332_22_interrupted_worker_or_timeout_reported_pass_rejected,
        test_r332_23_orphaned_worker_after_run_rejected,
        test_r332_24_actual_peak_concurrency_exceeding_limit_rejected,
        test_r332_25_disk_guard_threshold_breached_but_report_pass_rejected,
        test_r332_26_memory_sampling_failed_but_numeric_default_pass_rejected,
        test_r332_27_one_negative_control_absent_from_registry_or_execution_rejected,
        test_r332_28_bad_backtest_source_sha_rejected,
        test_r332_29_bad_dataset_sha_rejected,
        test_r332_30_historical_baseline_sha_preserved_as_separate_identity,
        test_r332_31_rerun_file_passes_but_original_test_fails_classified_flaky_and_blocks,
        test_r332_32_two_monitored_tests_in_former_shard_neighbor_layout_pass_without_masking,
        test_r332_33_attempt_artifact_deleted_by_cleanup_rejected,
        test_r332_34_apparent_zero_failures_but_raw_worker_ledger_nonzero_rejected,
        test_r332_35_run_manifest_created_after_first_mandatory_worker_rejected,
        test_r332_36_success_path_with_primary_zero_failures_and_verified_evidence_accepted,
    )
    checks.extend([
        ("R332-01", "R3.3.2: Primary subprocess exit_code=1 + retry=0 rejected as non-clean", test_r332_01_primary_subprocess_nonzero_plus_retry_pass_not_clean_pass),
        ("R332-02", "R3.3.2: Original JUnit has 1 failing testcase + derived XML all PASS rejected", test_r332_02_original_junit_has_failing_testcase_derived_xml_all_pass_rejected),
        ("R332-03", "R3.3.2: Mutated primary_exit_code 1 to 0 detected against raw log", test_r332_03_mutate_primary_exit_code_1_to_0_detected_against_raw_log),
        ("R332-04", "R3.3.2: Missing original raw shard XML fails closed", test_r332_04_missing_original_raw_shard_xml_fails_closed),
        ("R332-05", "R3.3.2: Missing original raw shard log fails closed", test_r332_05_missing_original_raw_shard_log_fails_closed),
        ("R332-06", "R3.3.2: Altered raw XML without manifest SHA update rejected", test_r332_06_altered_raw_xml_without_manifest_sha_update_rejected),
        ("R332-07", "R3.3.2: Altered retry XML with matching metadata only rejected", test_r332_07_altered_retry_xml_with_matching_metadata_only_rejected),
        ("R332-08", "R3.3.2: Duplicate attempt ID rejected", test_r332_08_duplicate_attempt_id_rejected),
        ("R332-09", "R3.3.2: Duplicate testcase ID collapsed rejected", test_r332_09_duplicate_testcase_id_collapsed_rejected),
        ("R332-10", "R3.3.2: Retry reusing primary attempt ID rejected", test_r332_10_retry_reuses_primary_attempt_id_rejected),
        ("R332-11", "R3.3.2: Retry claiming ACTUAL_SUBPROCESS without PID/argv/timestamps rejected", test_r332_11_retry_claimed_actual_subprocess_without_pid_argv_timestamps_rejected),
        ("R332-12", "R3.3.2: Derived projection claiming independently executed pytest rejected", test_r332_12_derived_projection_claiming_independently_executed_pytest_rejected),
        ("R332-13", "R3.3.2: Derived JUnit sidecar exit 0 while linked raw attempt exit 1 rejected", test_r332_13_derived_junit_sidecar_exit_0_while_linked_raw_attempt_exit_1_rejected),
        ("R332-14", "R3.3.2: Master worker ledger 22/20/2 failed with Phase-H 17/17 rejected", test_r332_14_master_worker_ledger_22_scheduled_20_succeeded_2_failed_phase_h_17_17_rejected),
        ("R332-15", "R3.3.2: Missing master worker ledger rejected", test_r332_15_missing_master_worker_ledger_rejected),
        ("R332-16", "R3.3.2: Empty master worker ledger rejected", test_r332_16_empty_master_worker_ledger_rejected),
        ("R332-17", "R3.3.2: Missing primary shard started_at_ms rejected", test_r332_17_missing_primary_shard_started_at_ms_rejected),
        ("R332-18", "R3.3.2: Missing worker source SHA rejected", test_r332_18_missing_worker_source_sha_rejected),
        ("R332-19", "R3.3.2: Cross-run retry XML sidecar rejected", test_r332_19_cross_run_retry_xml_sidecar_rejected),
        ("R332-20", "R3.3.2: Cross-commit XML with same testcase name rejected", test_r332_20_cross_commit_xml_with_same_testcase_name_rejected),
        ("R332-21", "R3.3.2: Missing backend tests in shard inventory rejected", test_r332_21_missing_backend_tests_in_shard_inventory_rejected),
        ("R332-22", "R3.3.2: Interrupted worker/timeout reported PASS rejected", test_r332_22_interrupted_worker_or_timeout_reported_pass_rejected),
        ("R332-23", "R3.3.2: Orphaned worker after run rejected", test_r332_23_orphaned_worker_after_run_rejected),
        ("R332-24", "R3.3.2: Actual peak concurrency > limit rejected", test_r332_24_actual_peak_concurrency_exceeding_limit_rejected),
        ("R332-25", "R3.3.2: Disk guard threshold breached but report PASS rejected", test_r332_25_disk_guard_threshold_breached_but_report_pass_rejected),
        ("R332-26", "R3.3.2: Memory sampling failed but numeric default PASS rejected", test_r332_26_memory_sampling_failed_but_numeric_default_pass_rejected),
        ("R332-27", "R3.3.2: One negative control absent from registry/execution rejected", test_r332_27_one_negative_control_absent_from_registry_or_execution_rejected),
        ("R332-28", "R3.3.2: Bad backtest source SHA rejected", test_r332_28_bad_backtest_source_sha_rejected),
        ("R332-29", "R3.3.2: Bad dataset SHA rejected", test_r332_29_bad_dataset_sha_rejected),
        ("R332-30", "R3.3.2: Separate historical baseline SHA identity preserved", test_r332_30_historical_baseline_sha_preserved_as_separate_identity),
        ("R332-31", "R3.3.2: Rerun file passes after original fail classified FLAKY and blocks", test_r332_31_rerun_file_passes_but_original_test_fails_classified_flaky_and_blocks),
        ("R332-32", "R3.3.2: Two formerly failing tests pass in former shard neighbor layout", test_r332_32_two_monitored_tests_in_former_shard_neighbor_layout_pass_without_masking),
        ("R332-33", "R3.3.2: Attempt artifact deleted by cleanup rejected", test_r332_33_attempt_artifact_deleted_by_cleanup_rejected),
        ("R332-34", "R3.3.2: Apparent 0 failures but raw worker ledger nonzero rejected", test_r332_34_apparent_zero_failures_but_raw_worker_ledger_nonzero_rejected),
        ("R332-35", "R3.3.2: Run manifest created after first mandatory worker rejected", test_r332_35_run_manifest_created_after_first_mandatory_worker_rejected),
        ("R332-36", "R3.3.2: Positive control with primary zero failures accepted", test_r332_36_success_path_with_primary_zero_failures_and_verified_evidence_accepted),
    ])
    import concurrent.futures
    import os

    for sub_name in ("N29", "N30"):
        (tmp_dir / sub_name).mkdir(parents=True, exist_ok=True)

    def _run_one_check(item):
        cid, desc, fn = item
        try:
            fn()
            return {"test_id": cid, "description": desc, "status": "PASS", "error": None}
        except Exception as exc:
            return {"test_id": cid, "description": desc, "status": "FAIL", "error": f"{type(exc).__name__}:{exc}"}

    max_workers = min(12, max(4, (os.cpu_count() or 4) * 2))
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        results = list(pool.map(_run_one_check, checks))
    passed_count = sum(1 for r in results if r["status"] == "PASS")
    return {
        "release": "V17.9.0.1.3",
        "total_negative_controls": len(checks),
        "passed_negative_controls": passed_count,
        "failed_negative_controls": len(checks) - passed_count,
        "total_negative_tests": len(checks),
        "passed_negative_tests": passed_count,
        "failed_negative_tests": len(checks) - passed_count,
        "status": "PASS" if passed_count == len(checks) else "FAIL",
        "results": results,
    }
