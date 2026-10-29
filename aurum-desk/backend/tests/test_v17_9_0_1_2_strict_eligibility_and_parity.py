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
    validate_canonical_executability,
)
from services.v17_8_backtest_runner import (
    _extract_historical_opportunities_chronologically,
    evaluate_and_simulate_variants_on_opportunities,
)
from services.v17_9_0_1_baseline_oracle import (
    _make_synthetic_candle_series,
    build_phase_b_characterization_scenarios,
    execute_isolated_historical_baseline_scenarios,
    materialize_historical_baseline_tree,
    verify_historical_baseline_commit_provenance,
    verify_historical_baseline_dependency_integrity,
)
from tools.acceptance_verdict import (
    determine_v17_9_0_1_top_level_verdict,
    normalize_v17_9_0_1_gate_status,
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
    assert len(iso["loaded_historical_modules"]) == 9
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


def run_all_v17_9_0_1_2_negative_acceptance_checks(tmp_dir: Path) -> Dict[str, Any]:
    """
    Executes all 30 mandatory negative acceptance tests (N01-N30) and returns
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
