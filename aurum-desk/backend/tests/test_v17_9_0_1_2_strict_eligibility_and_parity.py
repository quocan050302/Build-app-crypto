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
    evaluate_and_simulate_variants_on_opportunities,
    execute_chronological_backtest_window_for_parity,
    reconcile_backtest_trade_decision_lineage,
)
from services.v17_9_0_1_baseline_oracle import (
    _make_synthetic_candle_series,
    build_phase_b_characterization_scenarios,
    build_real_historical_baseline_scenarios,
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


def run_all_v17_9_0_1_2_negative_acceptance_checks(tmp_dir: Path) -> Dict[str, Any]:
    """
    Executes all 50 mandatory negative acceptance tests (N01-N50) and returns
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
