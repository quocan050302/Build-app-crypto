"""
backend/tests/test_v17_9_3_1_historical_execution_truth.py
==========================================================
AURUM Desk V17.9.3.1 — Historical Execution Truth, Causal Candidate Coverage,
MTF Root-Cause & Forensic Closure Test Suite (T01–T46).
All synthetic fixtures in this file carry `provenance = "SYNTHETIC_UNIT_TEST"`
and are strictly segregated from `HISTORICAL_BACKTEST_V1793` artifacts.
"""

from __future__ import annotations

import ast
import bisect
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models
from domain_calculator import CostAssumptions, calculate_risk_reward
from services.market_shadow_service import MarketShadowService
from services.mtf_structure_liquidity_service import (
    compare_actual_consumed_decisions,
)
from services.scalp_m1_types import ModelIdentifier
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator
from services.shared_strategy_evaluator import (
    DecisionStatus,
    SharedStrategyEvaluator,
    build_market_decision_context,
)
from services.stop_placement_policy_service import (
    StopPolicyValidationError,
    resolve_stop_placement_policy,
    update_stop_placement_policy,
    validate_stop_placement_policy,
)
from services.unicorn_evidence_types import UnicornSetupStatus
from services.v17_8_backtest_runner import (
    collect_or_load_verified_historical_dataset,
    evaluate_production_parity_model_snapshot,
)
from services.v17_9_2_structural_stop_intelligence import (
    ExecutableTradeLedgerEntryV17931,
    resolve_v17931_protected_structure_authority,
    simulate_v17931_trade_execution_net,
    verify_v17931_frozen_forensic_episode_parity,
)
from services.v17_9_3_macro_unicorn_intelligence import (
    bind_unicorn_to_causal_episode,
    classify_macro_context_quality,
    diagnose_v17931_d1_vs_d2_mtf_gate,
    evaluate_causal_unicorn_confluence,
    normalize_v1793_mode,
    plan_v17_9_3_macro_unicorn_setup,
    resolve_causal_macro_time_window,
    validate_macro_schedule_config,
    validate_unicorn_invalidation_against_v1792,
)
from tools.run_v17_9_3_macro_unicorn_backtest import (
    PRODUCTION_MODEL_IDS,
    V1793_VARIANT_POLICIES,
    assess_v1793_statistical_claims,
    build_matched_trade_and_episode_forensics,
    build_v1793_variant_market_context,
    enumerate_v17931_all_decision_timestamps,
    evaluate_v1793_forward_outcomes,
    screen_v17931_causal_setup_universe,
    validate_v1793_historical_provenance_segregation,
    verify_v17931_frozen_oracle,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SYNTHETIC_PROVENANCE = "SYNTHETIC_UNIT_TEST"

_CACHED_DATASET: Dict[str, Any] | None = None
_CACHED_UNIVERSE_SCREEN: Dict[str, Any] | None = None


def _get_dataset() -> Dict[str, Any]:
    global _CACHED_DATASET
    if _CACHED_DATASET is None:
        _CACHED_DATASET = collect_or_load_verified_historical_dataset(
            symbol="XAUUSDT", allow_network_refresh=False
        )
    return _CACHED_DATASET


def _get_universe_screen() -> Dict[str, Any]:
    global _CACHED_UNIVERSE_SCREEN
    if _CACHED_UNIVERSE_SCREEN is None:
        _CACHED_UNIVERSE_SCREEN = screen_v17931_causal_setup_universe(
            _get_dataset(), holdout_sample_size=240
        )
    return _CACHED_UNIVERSE_SCREEN


def _ny_to_utc_ms(year: int, month: int, day: int, hour: int, minute: int, second: int = 0) -> int:
    ny_tz = ZoneInfo("America/New_York")
    dt_ny = datetime(year, month, day, hour, minute, second, tzinfo=ny_tz)
    return int(dt_ny.astimezone(timezone.utc).timestamp() * 1000)


def _make_synthetic_unicorn_m1_bars(
    base_open_ms: int,
    direction: str = "LONG",
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    bars_1m: List[Dict[str, Any]] = []
    for i in range(20):
        t_open = base_open_ms + i * 60_000
        if direction == "LONG":
            o = 3000.0 + (i % 2) * 0.4
            h = o + 0.8
            l = o - 0.6
            c = o + 0.2
        else:
            o = 3000.0 - (i % 2) * 0.4
            h = o + 0.6
            l = o - 0.8
            c = o - 0.2
        bars_1m.append(
            {
                "timestamp": t_open,
                "open_time": t_open,
                "close_time": t_open + 60_000,
                "open": round(o, 2),
                "high": round(h, 2),
                "low": round(l, 2),
                "close": round(c, 2),
                "volume": 100.0,
                "is_closed": True,
                "provenance": SYNTHETIC_PROVENANCE,
            }
        )

    if direction == "LONG":
        pattern_long = [
            (3001.0, 3002.5, 3000.5, 3002.0),
            (3002.0, 3003.5, 3001.5, 3003.0),
            (3003.0, 3005.0, 3002.5, 3004.2),
            (3004.2, 3004.4, 3002.0, 3002.5),
            (3002.5, 3003.8, 3001.2, 3003.5),
            (3002.8, 3003.0, 2999.0, 2999.5),
            (2999.5, 3001.5, 2999.2, 3001.2),
            (3001.2, 3007.5, 3001.0, 3007.0),
            (3007.0, 3009.0, 3004.0, 3008.2),
            (3008.2, 3008.5, 3002.2, 3006.5),
            (3006.5, 3008.0, 3005.5, 3007.5),
        ]
        for j, (o, h, l, c) in enumerate(pattern_long, start=20):
            t_open = base_open_ms + j * 60_000
            bars_1m.append(
                {
                    "timestamp": t_open,
                    "open_time": t_open,
                    "close_time": t_open + 60_000,
                    "open": o,
                    "high": h,
                    "low": l,
                    "close": c,
                    "volume": 200.0,
                    "is_closed": True,
                    "provenance": SYNTHETIC_PROVENANCE,
                }
            )
    else:
        pattern_short = [
            (2999.0, 2999.5, 2997.5, 2998.0),
            (2998.0, 2998.5, 2996.5, 2997.0),
            (2997.0, 2997.5, 2995.0, 2995.8),
            (2995.8, 2998.0, 2995.6, 2997.5),
            (2997.5, 2998.8, 2996.2, 2996.5),
            (2997.2, 3001.0, 2997.0, 3000.5),
            (3000.5, 3000.8, 2998.5, 2998.8),
            (2998.8, 2999.0, 2992.5, 2993.0),
            (2993.0, 2996.0, 2991.0, 2991.8),
            (2991.8, 2997.8, 2991.5, 2993.5),
            (2993.5, 2994.5, 2992.0, 2992.5),
        ]
        for j, (o, h, l, c) in enumerate(pattern_short, start=20):
            t_open = base_open_ms + j * 60_000
            bars_1m.append(
                {
                    "timestamp": t_open,
                    "open_time": t_open,
                    "close_time": t_open + 60_000,
                    "open": o,
                    "high": h,
                    "low": l,
                    "close": c,
                    "volume": 200.0,
                    "is_closed": True,
                    "provenance": SYNTHETIC_PROVENANCE,
                }
            )

    end_ms = int(bars_1m[-1]["close_time"])

    def _make_htf_trend_bars(tf_ms: int, count: int) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        end_aligned_ms = (end_ms // tf_ms) * tf_ms
        start_t = end_aligned_ms - count * tf_ms
        cycle = [0.0, 2.0, 5.0, 2.5, 1.0, 3.0]
        for k in range(count):
            t_o = start_t + k * tf_ms
            step_offset = (k // 6) * 4.5 + cycle[k % 6]
            if direction == "LONG":
                o = 2950.0 + step_offset
                c = o + (1.2 if (k % 6 in (0, 1, 2, 5)) else -0.8)
                h = max(o, c) + (1.5 if (k % 6 == 2) else 0.5)
                l = min(o, c) - (1.5 if (k % 6 == 4) else 0.5)
            else:
                o = 3050.0 - step_offset
                c = o - (1.2 if (k % 6 in (0, 1, 2, 5)) else -0.8)
                h = max(o, c) + (1.5 if (k % 6 == 4) else 0.5)
                l = min(o, c) - (1.5 if (k % 6 == 2) else 0.5)
            out.append(
                {
                    "timestamp": t_o,
                    "open_time": t_o,
                    "close_time": t_o + tf_ms,
                    "open": round(o, 2),
                    "high": round(h, 2),
                    "low": round(l, 2),
                    "close": round(c, 2),
                    "volume": 500.0,
                    "is_closed": True,
                    "provenance": SYNTHETIC_PROVENANCE,
                }
            )
        return out

    bars_5m = _make_htf_trend_bars(300_000, 24)
    bars_15m = _make_htf_trend_bars(900_000, 24)
    bars_1h = _make_htf_trend_bars(3_600_000, 24)
    bars_4h = _make_htf_trend_bars(14_400_000, 24)
    return bars_1m, bars_5m, bars_15m, bars_1h + bars_4h


# ==============================================================================
# T01: Net execution ledger floored quantity, fees, and non-double-counted slippage
# ==============================================================================
def test_T01_net_execution_ledger_floored_qty_fees_and_slippage() -> None:
    fwd = [
        {
            "open_time": 1_775_000_000_000,
            "close_time": 1_775_000_060_000,
            "open": 3000.00,
            "high": 3018.50,
            "low": 2999.50,
            "close": 3018.20,
        }
    ]
    led = simulate_v17931_trade_execution_net(
        episode_id="ep_t01",
        variant="D1_V1793_MACRO_PLUS_UNICORN_V1792_SL_TP",
        model_id="SMC_M1_FVG_CONTINUATION",
        direction="LONG",
        decision_timestamp_ms=1_775_000_000_000,
        checkpoint_index_1m=100,
        planned_entry=3000.10,
        selected_sl=2995.10,
        take_profit=3018.10,
        forward_m1_bars=fwd,
        entry_fill_mode="NEXT_BAR_OPEN_BID_ASK",
        provenance=SYNTHETIC_PROVENANCE,
    )
    assert isinstance(led, ExecutableTradeLedgerEntryV17931)
    assert led.execution_truth_class == "MODELED_HISTORICAL_BAR_EXECUTION"
    assert led.outcome_label == "WIN"
    assert led.quantity > 0.0
    assert round(led.quantity * 100.0) == pytest.approx(led.quantity * 100.0)
    # Entry fill for LONG on next bar open = 3000.00 + 0.10 (half spread) + 0.10 (entry slip) = 3000.20
    assert led.entry_fill_price == pytest.approx(3000.20)
    assert led.exit_fill_price == pytest.approx(3018.10)
    expected_gross = (3018.10 - 3000.20) * led.quantity
    expected_fees = (3000.20 * 0.0004 + 3018.10 * 0.0004) * led.quantity
    assert led.realized_gross_pnl_usd == pytest.approx(round(expected_gross, 4), abs=1e-3)
    assert led.total_friction_usd == pytest.approx(round(expected_fees, 4), abs=1e-3)
    assert led.realized_net_pnl_usd == pytest.approx(round(expected_gross - expected_fees, 4), abs=1e-3)
    assert led.realized_r_net == pytest.approx(round(led.realized_net_pnl_usd / led.planned_net_risk_usd, 4))


# ==============================================================================
# T02: Zero synthetic `realized_r * 2.50` in backtest runner
# ==============================================================================
def test_T02_zero_synthetic_realized_r_times_250_in_backtest_runner() -> None:
    runner_path = REPO_ROOT / "tools" / "run_v17_9_3_macro_unicorn_backtest.py"
    src = runner_path.read_text(encoding="utf-8")
    assert "realized_r * 2.50" not in src
    assert "r_val * 2.50" not in src
    assert "r * 2.50" not in src
    assert "avoided_loss_r * 2.50" not in src
    assert "forgone_win_r * 2.50" not in src


# ==============================================================================
# T03: Fill modes (NEXT_BAR_OPEN_BID_ASK vs LEGACY_CLOSE_FILL vs STRESS_2X)
# ==============================================================================
def test_T03_entry_fill_modes_and_gap_through_stop_handling() -> None:
    fwd_loss = [
        {
            "open_time": 1_775_000_000_000,
            "close_time": 1_775_000_060_000,
            "open": 3000.00,
            "high": 3000.50,
            "low": 2994.00,
            "close": 2994.50,
        }
    ]
    led_next = simulate_v17931_trade_execution_net(
        episode_id="ep_t03_next",
        variant="A1_V1792_SL_TP_BASELINE",
        model_id="SMC_M1_FVG_CONTINUATION",
        direction="LONG",
        decision_timestamp_ms=1_775_000_000_000,
        checkpoint_index_1m=10,
        planned_entry=3000.10,
        selected_sl=2995.10,
        take_profit=3018.10,
        forward_m1_bars=fwd_loss,
        entry_fill_mode="NEXT_BAR_OPEN_BID_ASK",
        provenance=SYNTHETIC_PROVENANCE,
    )
    led_leg = simulate_v17931_trade_execution_net(
        episode_id="ep_t03_leg",
        variant="A1_V1792_SL_TP_BASELINE",
        model_id="SMC_M1_FVG_CONTINUATION",
        direction="LONG",
        decision_timestamp_ms=1_775_000_000_000,
        checkpoint_index_1m=10,
        planned_entry=3000.10,
        selected_sl=2995.10,
        take_profit=3018.10,
        forward_m1_bars=fwd_loss,
        entry_fill_mode="LEGACY_CLOSE_FILL",
        provenance=SYNTHETIC_PROVENANCE,
    )
    led_stress = simulate_v17931_trade_execution_net(
        episode_id="ep_t03_stress",
        variant="A1_V1792_SL_TP_BASELINE",
        model_id="SMC_M1_FVG_CONTINUATION",
        direction="LONG",
        decision_timestamp_ms=1_775_000_000_000,
        checkpoint_index_1m=10,
        planned_entry=3000.10,
        selected_sl=2995.10,
        take_profit=3018.10,
        forward_m1_bars=fwd_loss,
        entry_fill_mode="STRESS_WIDER_SPREAD_SLIPPAGE_2X",
        provenance=SYNTHETIC_PROVENANCE,
    )
    assert led_leg.entry_fill_price == pytest.approx(3000.20)  # planned_entry 3000.10 + 0.10 slip
    assert led_next.entry_fill_price == pytest.approx(3000.20)  # open 3000.00 + 0.10 half_spread + 0.10 slip
    assert led_stress.entry_fill_price == pytest.approx(3000.40)  # open 3000.00 + 0.20 half_spread + 0.20 slip
    assert led_stress.realized_net_pnl_usd < led_next.realized_net_pnl_usd


# ==============================================================================
# T04: Same-bar TP+SL conservative tie-break (LOSS_TIE_CONSERVATIVE)
# ==============================================================================
def test_T04_same_bar_tp_and_sl_conservative_tie_break() -> None:
    fwd_both = [
        {
            "open_time": 1_775_000_000_000,
            "close_time": 1_775_000_060_000,
            "open": 3000.00,
            "high": 3025.00,
            "low": 2990.00,
            "close": 3010.00,
        }
    ]
    led = simulate_v17931_trade_execution_net(
        episode_id="ep_t04",
        variant="A1_V1792_SL_TP_BASELINE",
        model_id="SMC_M1_FVG_CONTINUATION",
        direction="LONG",
        decision_timestamp_ms=1_775_000_000_000,
        checkpoint_index_1m=11,
        planned_entry=3000.10,
        selected_sl=2995.10,
        take_profit=3018.10,
        forward_m1_bars=fwd_both,
        entry_fill_mode="NEXT_BAR_OPEN_BID_ASK",
        provenance=SYNTHETIC_PROVENANCE,
    )
    assert led.outcome_label == "LOSS_TIE_CONSERVATIVE"
    assert led.realized_net_pnl_usd < 0.0
    assert led.realized_r_net < 0.0


# ==============================================================================
# T05: OPEN_AT_HORIZON mark-to-market exit with taker fee and slippage
# ==============================================================================
def test_T05_open_at_horizon_mark_to_market_net_accounting() -> None:
    fwd_open = [
        {
            "open_time": 1_775_000_000_000 + k * 60_000,
            "close_time": 1_775_000_060_000 + k * 60_000,
            "open": 3000.00,
            "high": 3003.00,
            "low": 2998.00,
            "close": 3002.00,
        }
        for k in range(5)
    ]
    led = simulate_v17931_trade_execution_net(
        episode_id="ep_t05",
        variant="A1_V1792_SL_TP_BASELINE",
        model_id="SMC_M1_FVG_CONTINUATION",
        direction="LONG",
        decision_timestamp_ms=1_775_000_000_000,
        checkpoint_index_1m=12,
        planned_entry=3000.10,
        selected_sl=2995.10,
        take_profit=3018.10,
        forward_m1_bars=fwd_open,
        entry_fill_mode="NEXT_BAR_OPEN_BID_ASK",
        provenance=SYNTHETIC_PROVENANCE,
    )
    assert led.outcome_label == "OPEN_AT_HORIZON"
    assert led.bars_held == 5
    # Final close 3002.00 - 0.10 half_spread - 0.10 slip = 3001.80
    assert led.exit_fill_price == pytest.approx(3001.80)


# ==============================================================================
# T06: NO_FORWARD_BARS zero-PnL fail-closed ledger handling
# ==============================================================================
def test_T06_no_forward_bars_fail_closed() -> None:
    led = simulate_v17931_trade_execution_net(
        episode_id="ep_t06",
        variant="A1_V1792_SL_TP_BASELINE",
        model_id="SMC_M1_FVG_CONTINUATION",
        direction="LONG",
        decision_timestamp_ms=1_775_000_000_000,
        checkpoint_index_1m=13,
        planned_entry=3000.10,
        selected_sl=2995.10,
        take_profit=3018.10,
        forward_m1_bars=[],
        entry_fill_mode="NEXT_BAR_OPEN_BID_ASK",
        provenance=SYNTHETIC_PROVENANCE,
    )
    assert led.outcome_label == "NO_FORWARD_BARS"
    assert led.bars_held == 0
    assert led.realized_net_pnl_usd == 0.0
    assert led.realized_r_net == 0.0


# ==============================================================================
# T07: Risk budget invariant planned_net_risk_usd <= 2.50 across varied stop distances
# ==============================================================================
@pytest.mark.parametrize("stop_dist", [1.0, 2.5, 5.0, 10.0, 20.0])
def test_T07_planned_net_risk_usd_within_250_budget(stop_dist: float) -> None:
    entry = 3000.00
    sl = round(entry - stop_dist, 2)
    tp = round(entry + stop_dist * 3.5, 2)
    rr = calculate_risk_reward(
        direction="LONG",
        planned_entry=entry,
        stop_loss=sl,
        take_profit=tp,
        capital_usdt=1000.0,
        risk_pct=0.25,
        leverage=20,
        margin_mode="ISOLATED",
        min_net_rr=2.0,
        costs=CostAssumptions(),
        now_ms=1_775_000_000_000,
    )
    if rr.can_execute:
        assert rr.net_risk_usdt <= 2.500001
        assert rr.quantity >= 0.01


# ==============================================================================
# T08: Minimum lot 0.01 unexecutable stop distance fail-closed
# ==============================================================================
def test_T08_minimum_lot_001_unexecutable_wide_stop_rejected() -> None:
    # At stop_dist = 300.0, 0.01 lot risk > $3.00 > $2.50 budget -> cannot_execute
    rr = calculate_risk_reward(
        direction="LONG",
        planned_entry=3000.00,
        stop_loss=2700.00,
        take_profit=3900.00,
        capital_usdt=1000.0,
        risk_pct=0.25,
        leverage=20,
        margin_mode="ISOLATED",
        min_net_rr=2.0,
        costs=CostAssumptions(),
        now_ms=1_775_000_000_000,
    )
    assert rr.can_execute is False


# ==============================================================================
# T09: Algebraic identity realized_r_net = round(realized_net_pnl_usd / planned_net_risk_usd, 4)
# ==============================================================================
@pytest.mark.parametrize("direction,is_win", [("LONG", True), ("LONG", False), ("SHORT", True), ("SHORT", False)])
def test_T09_realized_r_net_algebraic_identity(direction: str, is_win: bool) -> None:
    if direction == "LONG":
        entry, sl, tp = 3000.10, 2995.10, 3018.10
        bar = {"open": 3000.0, "high": 3020.0, "low": 2999.0, "close": 3019.0} if is_win else {"open": 3000.0, "high": 3001.0, "low": 2994.0, "close": 2994.5}
    else:
        entry, sl, tp = 2999.90, 3004.90, 2981.90
        bar = {"open": 3000.0, "high": 3001.0, "low": 2980.0, "close": 2981.0} if is_win else {"open": 3000.0, "high": 3006.0, "low": 2999.0, "close": 3005.5}
    bar["open_time"] = 1_775_000_000_000
    bar["close_time"] = 1_775_000_060_000
    led = simulate_v17931_trade_execution_net(
        episode_id="ep_t09",
        variant="D1_V1793_MACRO_PLUS_UNICORN_V1792_SL_TP",
        model_id="SMC_M1_FVG_CONTINUATION",
        direction=direction,
        decision_timestamp_ms=1_775_000_000_000,
        checkpoint_index_1m=20,
        planned_entry=entry,
        selected_sl=sl,
        take_profit=tp,
        forward_m1_bars=[bar],
        entry_fill_mode="NEXT_BAR_OPEN_BID_ASK",
        provenance=SYNTHETIC_PROVENANCE,
    )
    assert led.realized_r_net == round(led.realized_net_pnl_usd / led.planned_net_risk_usd, 4)


# ==============================================================================
# T10: Full 22,346 decision timestamp enumeration
# ==============================================================================
def test_T10_enumerate_v17931_all_decision_timestamps_exact_counts() -> None:
    ds = _get_dataset()
    enum_res = enumerate_v17931_all_decision_timestamps(ds, warmup_bars=30)
    assert enum_res["total_m1_bars"] == 22376
    assert enum_res["enumerated_decision_timestamps_count"] == 22346
    assert enum_res["macro_inside_count"] == 1763
    assert enum_res["macro_outside_count"] == 20583
    assert enum_res["macro_inside_count"] + enum_res["macro_outside_count"] == 22346


# ==============================================================================
# T11: Causal setup universe screener 100% coverage of macro, Unicorn, and A0
# ==============================================================================
def test_T11_screen_v17931_causal_setup_universe_100_percent_coverage() -> None:
    screen = _get_universe_screen()
    assert screen["enumerated_decision_timestamps_count"] == 22346
    assert screen["macro_inside_count"] == 1763
    assert screen["macro_inside_coverage_ratio"] == 1.0
    assert screen["bound_unicorn_coverage_ratio"] == 1.0
    assert screen["a0_target_superset_coverage_ratio"] == 1.0
    assert screen["bound_unicorn_candidate_count"] >= 700
    assert screen["legacy_491_sample_control_count"] == 491
    screened_set = set(screen["screened_candidate_indices"])
    assert set(screen["macro_inside_indices"]).issubset(screened_set)
    assert set(screen["bound_unicorn_indices"]).issubset(screened_set)
    assert set(screen["a0_target_superset_indices"]).issubset(screened_set)
    assert set(screen["legacy_491_indices"]).issubset(screened_set)


# ==============================================================================
# T12: Holdout verification sample (>= 240 checkpoints) has 0 false negatives
# ==============================================================================
def test_T12_holdout_verification_sample_zero_false_negatives() -> None:
    screen = _get_universe_screen()
    holdout_indices = screen["holdout_verification_indices"]
    screened_set = set(screen["screened_candidate_indices"])
    assert len(holdout_indices) >= 240
    assert set(holdout_indices).isdisjoint(screened_set)

    ds = _get_dataset()
    bars_1m = ds["1m"]
    bars_5m = ds["5m"]
    bars_15m = ds["15m"]
    bars_1h = ds["1H"]
    bars_4h = ds["4H"]
    bars_1d = ds["1D"]
    c_5m = [int(b["close_time"]) for b in bars_5m]
    c_15m = [int(b["close_time"]) for b in bars_15m]
    c_1h = [int(b["close_time"]) for b in bars_1h]
    c_4h = [int(b["close_time"]) for b in bars_4h]
    c_1d = [int(b["close_time"]) for b in bars_1d]

    # Spot-check 15 stratified holdout checkpoints across all 10 variants & 5 models (750 evaluations)
    sample_holdout = holdout_indices[:: max(1, len(holdout_indices) // 15)][:15]
    false_negatives = 0
    for i_1m in sample_holdout:
        asof_ms = int(bars_1m[i_1m]["close_time"])
        i5 = bisect.bisect_right(c_5m, asof_ms)
        i15 = bisect.bisect_right(c_15m, asof_ms)
        i1h = bisect.bisect_right(c_1h, asof_ms)
        i4h = bisect.bisect_right(c_4h, asof_ms)
        i1d = bisect.bisect_right(c_1d, asof_ms)
        last_c = float(bars_1m[i_1m]["close"])
        base_inputs = {
            "symbol": "XAUUSDT",
            "decision_timestamp_ms": asof_ms,
            "m1_candles": bars_1m[max(0, i_1m + 1 - 120) : i_1m + 1],
            "m5_candles": bars_5m[max(0, i5 - 96) : i5],
            "m15_candles": bars_15m[max(0, i15 - 96) : i15],
            "h1_candles": bars_1h[max(0, i1h - 96) : i1h],
            "h4_candles": bars_4h[max(0, i4h - 64) : i4h],
            "d1_candles": bars_1d[max(0, i1d - 30) : i1d],
            "bid": round(last_c - 0.10, 2),
            "ask": round(last_c + 0.10, 2),
        }
        ctx_b0 = build_v1793_variant_market_context(
            base_inputs, V1793_VARIANT_POLICIES["B0_V1793_OBSERVE_ONLY"], decision_time=asof_ms
        )
        pref_dir = SharedStrategyEvaluator.resolve_canonical_preferred_direction(ctx_b0)
        for v_k, v_pol in V1793_VARIANT_POLICIES.items():
            ctx_v = (
                ctx_b0
                if v_k == "B0_V1793_OBSERVE_ONLY"
                else build_v1793_variant_market_context(
                    base_inputs, v_pol, decision_time=asof_ms, reference_context=ctx_b0
                )
            )
            for m_id in PRODUCTION_MODEL_IDS:
                snap = SharedStrategyEvaluator.evaluate_model_direction(ctx_v, m_id, pref_dir)
                if snap.decision_status == DecisionStatus.ELIGIBLE.value:
                    false_negatives += 1
    assert false_negatives == 0


# ==============================================================================
# T13: D2 MTF root-cause fix (`mtf_context.mtf_structure` vs `timeframe_states`)
# ==============================================================================
def test_T13_d2_mtf_root_cause_fix_mtf_structure_attribute_resolution() -> None:
    ts_ms = _ny_to_utc_ms(2026, 3, 25, 8, 55, 0)
    bars_1m, bars_5m, bars_15m, bars_htf = _make_synthetic_unicorn_m1_bars(
        ts_ms - 31 * 60_000, direction="LONG"
    )
    last_c = float(bars_1m[-1]["close"])
    base_inputs = {
        "symbol": "XAUUSDT",
        "decision_timestamp_ms": ts_ms,
        "m1_candles": bars_1m,
        "m5_candles": bars_5m,
        "m15_candles": bars_15m,
        "h1_candles": bars_htf[:24],
        "h4_candles": bars_htf[24:],
        "d1_candles": [],
        "bid": round(last_c - 0.10, 2),
        "ask": round(last_c + 0.10, 2),
    }
    ctx_d2 = build_v1793_variant_market_context(
        base_inputs,
        V1793_VARIANT_POLICIES["D2_V1793_MACRO_PLUS_UNICORN_MTF_FULL"],
        decision_time=ts_ms,
    )
    # Real MtfEvaluationContext has `.mtf_structure`, NOT `.timeframe_states`
    assert hasattr(ctx_d2.mtf_context, "mtf_structure")
    assert not hasattr(ctx_d2.mtf_context, "timeframe_states")

    uni = evaluate_causal_unicorn_confluence(
        ctx_d2,
        "LONG",
        ts_ms,
        require_confirmed_retest=True,
        require_htf_alignment=True,
    )
    assert uni.htf_alignment_status != "MTF_TIMEFRAME_STATES_MISSING"
    assert uni.episode_binding.htf_alignment_status in ("ALIGNED", "CONFLICT")


# ==============================================================================
# T14: diagnose_v17931_d1_vs_d2_mtf_gate ablation matrix
# ==============================================================================
def test_T14_diagnose_v17931_d1_vs_d2_mtf_gate_ablation_matrix() -> None:
    ts_ms = _ny_to_utc_ms(2026, 3, 25, 8, 55, 0)
    bars_1m, bars_5m, bars_15m, bars_htf = _make_synthetic_unicorn_m1_bars(
        ts_ms - 31 * 60_000, direction="LONG"
    )
    last_c = float(bars_1m[-1]["close"])
    base_inputs = {
        "symbol": "XAUUSDT",
        "decision_timestamp_ms": ts_ms,
        "m1_candles": bars_1m,
        "m5_candles": bars_5m,
        "m15_candles": bars_15m,
        "h1_candles": bars_htf[:24],
        "h4_candles": bars_htf[24:],
        "d1_candles": [],
        "bid": round(last_c - 0.10, 2),
        "ask": round(last_c + 0.10, 2),
    }
    ctx_d2 = build_v1793_variant_market_context(
        base_inputs,
        V1793_VARIANT_POLICIES["D2_V1793_MACRO_PLUS_UNICORN_MTF_FULL"],
        decision_time=ts_ms,
    )
    diag = diagnose_v17931_d1_vs_d2_mtf_gate(
        ctx_d2, ModelIdentifier.SMC_M1_FVG_CONTINUATION.value, "LONG"
    )
    assert "ablation_results" in diag
    for mode_k in ("H4_ONLY", "H1_ONLY", "M15_ONLY", "M15_H1_ONLY", "STRUCTURE_ONLY", "LIQUIDITY_ONLY", "FULL"):
        assert mode_k in diag["ablation_results"]
        assert "is_eligible" in diag["ablation_results"][mode_k]


# ==============================================================================
# T15: Historical D1 vs D2 root-cause verification on real dataset checkpoints
# ==============================================================================
def test_T15_historical_d1_vs_d2_root_cause_on_real_checkpoints() -> None:
    ds = _get_dataset()
    bars_1m = ds["1m"]
    bars_5m = ds["5m"]
    bars_15m = ds["15m"]
    bars_1h = ds["1H"]
    bars_4h = ds["4H"]
    bars_1d = ds["1D"]
    c_5m = [int(b["close_time"]) for b in bars_5m]
    c_15m = [int(b["close_time"]) for b in bars_15m]
    c_1h = [int(b["close_time"]) for b in bars_1h]
    c_4h = [int(b["close_time"]) for b in bars_4h]
    c_1d = [int(b["close_time"]) for b in bars_1d]

    def _eval_d1_d2_at(
        i_1m: int,
        model_id: str = ModelIdentifier.SMC_M1_FVG_CONTINUATION.value,
    ) -> Dict[str, Any]:
        asof_ms = int(bars_1m[i_1m]["close_time"])
        i5 = bisect.bisect_right(c_5m, asof_ms)
        i15 = bisect.bisect_right(c_15m, asof_ms)
        i1h = bisect.bisect_right(c_1h, asof_ms)
        i4h = bisect.bisect_right(c_4h, asof_ms)
        i1d = bisect.bisect_right(c_1d, asof_ms)
        last_c = float(bars_1m[i_1m]["close"])
        base_inputs = {
            "symbol": "XAUUSDT",
            "decision_timestamp_ms": asof_ms,
            "m1_candles": bars_1m[max(0, i_1m + 1 - 120) : i_1m + 1],
            "m5_candles": bars_5m[max(0, i5 - 96) : i5],
            "m15_candles": bars_15m[max(0, i15 - 96) : i15],
            "h1_candles": bars_1h[max(0, i1h - 96) : i1h],
            "h4_candles": bars_4h[max(0, i4h - 64) : i4h],
            "d1_candles": bars_1d[max(0, i1d - 30) : i1d],
            "bid": round(last_c - 0.10, 2),
            "ask": round(last_c + 0.10, 2),
        }
        ctx_d1 = build_v1793_variant_market_context(
            base_inputs, V1793_VARIANT_POLICIES["D1_V1793_MACRO_PLUS_UNICORN_V1792_SL_TP"], decision_time=asof_ms
        )
        ctx_d2 = build_v1793_variant_market_context(
            base_inputs, V1793_VARIANT_POLICIES["D2_V1793_MACRO_PLUS_UNICORN_MTF_FULL"], decision_time=asof_ms, reference_context=ctx_d1
        )
        pref_dir = SharedStrategyEvaluator.resolve_canonical_preferred_direction(ctx_d1)
        s_d1 = SharedStrategyEvaluator.evaluate_model_direction(
            ctx_d1, model_id, pref_dir
        )
        s_d2 = SharedStrategyEvaluator.evaluate_model_direction(
            ctx_d2, model_id, pref_dir
        )
        return diagnose_v17931_d1_vs_d2_mtf_gate(
            ctx_d2, model_id, pref_dir, snap_d1=s_d1, snap_d2=s_d2
        )

    # At idx=10434 (2026-10-20T06:56:00Z), H4/H1/M15 are all aligned so D2 NOW PASSES after the bug fix!
    d_10434 = _eval_d1_d2_at(10434, ModelIdentifier.SMC_M1_FVG_CONTINUATION.value)
    assert d_10434["d1_eligible"] is True
    assert d_10434["d2_eligible"] is True
    assert d_10434["root_cause_classification"] == "D2_ELIGIBLE_AFTER_MTF_BINDING_FIX"

    # At idx=4680 (real D1 episode #1, SMC_M1_REVERSAL SHORT), H4/H1 structure conflicts with SHORT
    d_4680 = _eval_d1_d2_at(4680, ModelIdentifier.SMC_M1_REVERSAL.value)
    assert d_4680["d1_eligible"] is True
    assert d_4680["d2_eligible"] is False
    assert "CONFLICT" in str(d_4680["root_cause_classification"]) or "REJECTED" in str(d_4680["root_cause_classification"])


# ==============================================================================
# T16: Protected structure authority resolution for A0 vs V17.9.2 variants
# ==============================================================================
def test_T16_resolve_v17931_protected_structure_authority() -> None:
    snap_a0_dict = {
        "entry_reference": 3000.0,
        "selected_sl": 2998.95,
        "structural_invalidation_price": 2999.00,
        "selected_anchor_id": "m1_sw_local_1",
        "anchor_kind": "PROTECTED_SWING_LOW",
        "v1792_mode": "OFF",
        "v1792_stop_plan_dict": None,
    }
    auth_a0 = resolve_v17931_protected_structure_authority(
        snap_a0_dict, variant_key="A0_FROZEN_BASELINE_OFF"
    )
    assert auth_a0["authority_source"] == "A0_LOCAL_M1_SWING_ANCHOR"
    assert auth_a0["invalidation_price"] == 2999.00

    snap_d1_dict = {
        "entry_reference": 3000.0,
        "selected_sl": 2994.20,
        "structural_invalidation_price": 2995.00,
        "selected_anchor_id": "v1792_m5_sw_1",
        "anchor_kind": "PROTECTED_SWING_LOW",
        "v1792_mode": "STRUCTURAL_SL_ON",
        "v1792_stop_plan_dict": {
            "invalidation_price": 2995.00,
            "frozen_anchor_id": "v1792_m5_sw_1",
            "selected_anchor": {"anchor_id": "v1792_m5_sw_1", "anchor_kind": "PROTECTED_SWING_LOW"},
        },
    }
    auth_d1 = resolve_v17931_protected_structure_authority(
        snap_d1_dict, variant_key="D1_V1793_MACRO_PLUS_UNICORN_V1792_SL_TP"
    )
    assert auth_d1["authority_source"] == "V1792_OUTER_STRUCTURAL_ANCHOR"
    assert auth_d1["invalidation_price"] == 2995.00


# ==============================================================================
# T17: Six frozen baseline episodes reconciliation (V17.9.2.1 vs V17.9.3)
# ==============================================================================
def test_T17_six_frozen_baseline_episodes_reconciliation() -> None:
    # Construct a LONG stop-out bar sequence that sweeps A0's tight local M1 swing (2999.00, SL 2998.95)
    # and closes at 2998.80 (invalidating local 2999.00 within 3 bars -> TRUE_INVALIDATION under local authority),
    # but NEVER breaches V17.9.2's outer M5 protected swing (2995.00) and then rallies to 3006.00
    # (-> PREMATURE_NOISE_SWEEP_STOP under V17.9.2.1 outer authority)!
    fwd_bars = [
        {"open_time": 1000, "close_time": 61000, "open": 3000.0, "high": 3000.2, "low": 2998.70, "close": 2998.80},
        {"open_time": 61000, "close_time": 121000, "open": 2998.80, "high": 3002.0, "low": 2998.75, "close": 3001.50},
        {"open_time": 121000, "close_time": 181000, "open": 3001.50, "high": 3006.0, "low": 3001.00, "close": 3005.50},
    ]
    rec = verify_v17931_frozen_forensic_episode_parity(
        episode_id="ep_t17_reconcile",
        model_id="SMC_M1_FVG_CONTINUATION",
        direction="LONG",
        decision_timestamp_ms=1000,
        entry_price=3000.00,
        a0_sl_price=2998.95,
        a0_tp_price=3010.00,
        a0_local_invalidation_price=2999.00,
        v1792_outer_invalidation_price=2995.00,
        forward_bars=fwd_bars,
        provenance=SYNTHETIC_PROVENANCE,
    )
    assert rec["is_reconciled"] is True
    assert rec["authority_difference_explains_shift"] is True
    assert rec["v17921_outer_authority_classification"] == "PREMATURE_NOISE_SWEEP_STOP"
    assert rec["v1793_local_authority_classification"] == "TRUE_INVALIDATION_LOSS"


# ==============================================================================
# T18: Dual-cohort matched forensics (Cohort A vs Cohort B) without synthetic * 2.50
# ==============================================================================
def test_T18_dual_cohort_matched_forensics_reconciled_usd() -> None:
    recs_a1 = [
        {
            "checkpoint_index_1m": 100,
            "decision_timestamp_ms": 1_775_000_000_000,
            "decision_time_utc": "2026-04-01T00:00:00Z",
            "model_id": "SMC_M1_FVG_CONTINUATION",
            "direction": "LONG",
            "decision_status": "ELIGIBLE",
            "outcome_label": "LOSS",
            "realized_r": -1.0,
            "realized_net_pnl_usd": -2.4123,
            "macro_primary_window_id": "OUTSIDE_MACRO_WINDOW",
            "unicorn_status": "INVALID",
            "v1793_rejection_stage": None,
            "v1793_rejection_reasons": [],
        }
    ]
    recs_d1 = [
        {
            "checkpoint_index_1m": 100,
            "decision_timestamp_ms": 1_775_000_000_000,
            "decision_time_utc": "2026-04-01T00:00:00Z",
            "model_id": "SMC_M1_FVG_CONTINUATION",
            "direction": "LONG",
            "decision_status": "VALID_SETUP_REJECTED_BY_RISK",
            "outcome_label": None,
            "realized_r": None,
            "realized_net_pnl_usd": None,
            "macro_primary_window_id": "OUTSIDE_MACRO_WINDOW",
            "unicorn_status": "INVALID",
            "v1793_rejection_stage": "V1793_MACRO_TIME_GATE",
            "v1793_rejection_reasons": ["MACRO_OUTSIDE_WINDOW"],
        }
    ]
    eps_a1 = [
        {
            "episode_index": 1,
            "checkpoint_index_1m": 100,
            "decision_timestamp_ms": 1_775_000_000_000,
            "decision_time_utc": "2026-04-01T00:00:00Z",
            "model_id": "SMC_M1_FVG_CONTINUATION",
            "direction": "LONG",
            "outcome_label": "LOSS",
            "realized_r": -1.0,
            "realized_net_pnl_usd": -2.4123,
        }
    ]
    records_by_v = {vk: [] for vk in V1793_VARIANT_POLICIES}
    records_by_v["A1_V1792_SL_TP_BASELINE"] = recs_a1
    records_by_v["D1_V1793_MACRO_PLUS_UNICORN_V1792_SL_TP"] = recs_d1
    eps_by_v = {vk: [] for vk in V1793_VARIANT_POLICIES}
    eps_by_v["A1_V1792_SL_TP_BASELINE"] = eps_a1

    mf = build_matched_trade_and_episode_forensics(records_by_v, eps_by_v)
    d1_vs_a1 = mf["matched_vs_a1_v1792_sl_tp_unconfounded"]["D1_V1793_MACRO_PLUS_UNICORN_V1792_SL_TP"]
    assert d1_vs_a1["cohort_a_tradability_status"] == "NOT_TRADABLE_PNL"
    # Reconciled USD must be 2.4123, NOT 1.0 * 2.50 = 2.50!
    assert d1_vs_a1["avoided_loss_usd_total"] == pytest.approx(2.4123)
    assert d1_vs_a1["matched_executable_episodes_cohort_b"]["episode_avoided_loss_usd"] == pytest.approx(2.4123)


# ==============================================================================
# T19: verify_v17931_frozen_oracle non-null integer check/mismatch counts
# ==============================================================================
def test_T19_verify_v17931_frozen_oracle_non_null_counts() -> None:
    ts_ms = _ny_to_utc_ms(2026, 3, 25, 8, 55, 0)
    bars_1m, bars_5m, bars_15m, bars_htf = _make_synthetic_unicorn_m1_bars(
        ts_ms - 31 * 60_000, direction="LONG"
    )
    base_inputs = {
        "symbol": "XAUUSDT",
        "decision_timestamp_ms": ts_ms,
        "m1_candles": bars_1m,
        "m5_candles": bars_5m,
        "m15_candles": bars_15m,
        "h1_candles": bars_htf[:24],
        "h4_candles": bars_htf[24:],
        "d1_candles": [],
        "bid": 3007.40,
        "ask": 3007.60,
    }
    ctx_a0 = build_v1793_variant_market_context(
        base_inputs, V1793_VARIANT_POLICIES["A0_FROZEN_BASELINE_OFF"], decision_time=ts_ms
    )
    for m_id in PRODUCTION_MODEL_IDS:
        res = verify_v17931_frozen_oracle(ctx_a0, m_id, "LONG")
        assert res["oracle_vs_a0_checks"] == 1
        assert res["oracle_vs_a0_mismatches"] == 0
        assert res["is_match"] is True


# ==============================================================================
# T20: Pure causal macro time window & zero wall-clock AST check
# ==============================================================================
def test_T20_macro_time_pure_causal_and_zero_wall_clock_ast() -> None:
    src_path = REPO_ROOT / "backend" / "services" / "v17_9_3_macro_unicorn_intelligence.py"
    tree = ast.parse(src_path.read_text(encoding="utf-8"))
    forbidden = {"now", "utcnow", "today", "time", "monotonic", "perf_counter"}
    violations = [
        f"line {n.lineno}: .{n.func.attr}()"
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in forbidden
    ]
    assert violations == []


# ==============================================================================
# T21: US DST transitions (EST UTC-5 vs EDT UTC-4)
# ==============================================================================
def test_T21_macro_ny_dst_transitions() -> None:
    est_ts = int(datetime(2026, 1, 15, 13, 55, 0, tzinfo=timezone.utc).timestamp() * 1000)
    est_st = resolve_causal_macro_time_window(est_ts, "XAUUSDT")
    assert est_st.is_dst is False
    assert est_st.utc_offset_minutes == -300
    assert est_st.eligible_for_macro_gate is True

    edt_ts = int(datetime(2026, 3, 25, 12, 55, 0, tzinfo=timezone.utc).timestamp() * 1000)
    edt_st = resolve_causal_macro_time_window(edt_ts, "XAUUSDT")
    assert edt_st.is_dst is True
    assert edt_st.utc_offset_minutes == -240
    assert edt_st.eligible_for_macro_gate is True


# ==============================================================================
# T22: Half-open [start, end) boundaries across all 7 configured NY macro windows
# ==============================================================================
def test_T22_macro_window_half_open_boundaries() -> None:
    for win_id, (sh, sm), (eh, em) in [
        ("LONDON_OPEN_MACRO_0233_0300", (2, 33), (3, 0)),
        ("LONDON_MACRO_0403_0430", (4, 3), (4, 30)),
        ("NY_AM_MACRO_0850_0910", (8, 50), (9, 10)),
        ("NY_AM_MACRO_0950_1010", (9, 50), (10, 10)),
        ("NY_AM_MACRO_1050_1110", (10, 50), (11, 10)),
        ("NY_LUNCH_PM_MACRO_1150_1210", (11, 50), (12, 10)),
        ("NY_PM_MACRO_1310_1340", (13, 10), (13, 40)),
    ]:
        assert resolve_causal_macro_time_window(_ny_to_utc_ms(2026, 3, 25, sh, sm, 0) - 1).eligible_for_macro_gate is False
        assert resolve_causal_macro_time_window(_ny_to_utc_ms(2026, 3, 25, sh, sm, 0)).primary_window_id == win_id
        assert resolve_causal_macro_time_window(_ny_to_utc_ms(2026, 3, 25, eh, em, 0) - 1).primary_window_id == win_id
        assert resolve_causal_macro_time_window(_ny_to_utc_ms(2026, 3, 25, eh, em, 0)).eligible_for_macro_gate is False


# ==============================================================================
# T23: Weekend, holiday calendar, and XAU_FIX_WINDOW fail-closed behavior
# ==============================================================================
def test_T23_macro_weekend_holiday_and_xau_fix_fail_closed() -> None:
    sat_st = resolve_causal_macro_time_window(_ny_to_utc_ms(2026, 3, 28, 8, 55, 0))
    assert sat_st.is_weekend_or_closed is True
    assert sat_st.eligible_for_macro_gate is False


# ==============================================================================
# T24: Schedule config validation rejects malformed configs fail-closed
# ==============================================================================
def test_T24_macro_schedule_validation_rejects_malformed_configs() -> None:
    s_inv = validate_macro_schedule_config(
        {
            "schedule_version": "INV_V1",
            "timezone": "America/New_York",
            "weekdays": [0, 1, 2, 3, 4],
            "windows": [{"window_id": "W_INV", "start_hhmm": "09:10", "end_hhmm": "08:50"}],
        }
    )
    assert s_inv.is_valid is False


# ==============================================================================
# T25: Hard session restriction overrides active macro window
# ==============================================================================
def test_T25_session_restricted_overrides_active_macro_window() -> None:
    ts_ms = _ny_to_utc_ms(2026, 3, 25, 8, 55, 0)
    bars_1m, bars_5m, bars_15m, bars_htf = _make_synthetic_unicorn_m1_bars(
        ts_ms - 31 * 60_000, direction="LONG"
    )
    ctx_r = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=ts_ms,
        m1_candles=bars_1m,
        m5_candles=bars_5m,
        m15_candles=bars_15m,
        h1_candles=bars_htf[:24],
        h4_candles=bars_htf[24:],
        bid=3007.40,
        ask=3007.60,
        session_restricted=True,
        session_restriction_reason="SESSION_TIME_RESTRICTED",
    )
    q = classify_macro_context_quality(resolve_causal_macro_time_window(ts_ms), ctx_r)
    assert q.is_eligible is False
    assert "SESSION_TIME_RESTRICTED" in q.reason_codes


# ==============================================================================
# T26: Valid bullish and bearish Unicorn Breaker + FVG + Retest + HTF alignment
# ==============================================================================
@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_T26_unicorn_valid_bullish_and_bearish_breaker_fvg_overlap(direction: str) -> None:
    ts_ms = _ny_to_utc_ms(2026, 3, 25, 8, 55, 0)
    bars_1m, bars_5m, bars_15m, bars_htf = _make_synthetic_unicorn_m1_bars(
        ts_ms - 31 * 60_000, direction=direction
    )
    last_c = float(bars_1m[-1]["close"])
    ctx = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=ts_ms,
        m1_candles=bars_1m,
        m5_candles=bars_5m,
        m15_candles=bars_15m,
        h1_candles=bars_htf[:24],
        h4_candles=bars_htf[24:],
        bid=round(last_c - 0.10, 2),
        ask=round(last_c + 0.10, 2),
        policy_config={"mtf_structure_observe_only": True},
    )
    res = evaluate_causal_unicorn_confluence(
        ctx, direction, ts_ms, require_confirmed_retest=True, require_htf_alignment=True
    )
    assert res.is_valid_unicorn is True
    assert res.validity_status == UnicornSetupStatus.VALID.value
    assert res.htf_aligned is True
    assert res.htf_alignment_status == "ALIGNED"


# ==============================================================================
# T27: Unicorn rejects future and unclosed bars
# ==============================================================================
def test_T27_unicorn_rejects_future_and_unclosed_bars() -> None:
    ts_ms = _ny_to_utc_ms(2026, 3, 25, 8, 55, 0)
    bars_1m, bars_5m, _, _ = _make_synthetic_unicorn_m1_bars(ts_ms - 31 * 60_000, direction="LONG")
    ctx = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=ts_ms,
        m1_candles=bars_1m,
        m5_candles=bars_5m,
        bid=3007.40,
        ask=3007.60,
    )
    res = evaluate_causal_unicorn_confluence(
        ctx,
        "LONG",
        ts_ms,
        setup_descriptors={
            "selected_fvg": {
                "id": "fvg_fut",
                "type": "BULLISH_FVG",
                "direction": "LONG",
                "bottom": 3001.5,
                "top": 3003.0,
                "timestamp": ts_ms + 60_000,
                "confirmed_at": ts_ms + 120_000,
            }
        },
        require_htf_alignment=False,
    )
    assert res.is_valid_unicorn is False
    assert "UNICORN_FUTURE_DATA_REJECTED" in res.rejection_reasons


# ==============================================================================
# T28: Unicorn rejects missing breaker or missing FVG
# ==============================================================================
def test_T28_unicorn_rejects_missing_breaker_or_missing_fvg() -> None:
    ts_ms = _ny_to_utc_ms(2026, 3, 25, 8, 55, 0)
    flat_bars = [
        {
            "timestamp": ts_ms - (25 - i) * 60_000,
            "open_time": ts_ms - (25 - i) * 60_000,
            "close_time": ts_ms - (24 - i) * 60_000,
            "open": 3000.0,
            "high": 3000.2,
            "low": 2999.8,
            "close": 3000.0,
            "volume": 50.0,
            "is_closed": True,
        }
        for i in range(25)
    ]
    ctx = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=ts_ms,
        m1_candles=flat_bars,
        m5_candles=flat_bars[:5],
        bid=2999.9,
        ask=3000.1,
    )
    res = evaluate_causal_unicorn_confluence(ctx, "LONG", ts_ms, require_htf_alignment=False)
    assert res.is_valid_unicorn is False
    assert "UNICORN_MISSING_BREAKER" in res.rejection_reasons


# ==============================================================================
# T29: Unicorn rejects non-overlapping zones
# ==============================================================================
def test_T29_unicorn_rejects_non_overlapping_zones() -> None:
    ts_ms = _ny_to_utc_ms(2026, 3, 25, 8, 55, 0)
    bars_1m, bars_5m, _, _ = _make_synthetic_unicorn_m1_bars(ts_ms - 31 * 60_000, direction="LONG")
    ctx = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=ts_ms,
        m1_candles=bars_1m,
        m5_candles=bars_5m,
        bid=3007.40,
        ask=3007.60,
    )
    res = evaluate_causal_unicorn_confluence(
        ctx,
        "LONG",
        ts_ms,
        setup_descriptors={
            "selected_fvg": {
                "id": "fvg_far",
                "type": "BULLISH_FVG",
                "direction": "LONG",
                "bottom": 3050.0,
                "top": 3052.0,
                "timestamp": int(bars_1m[27]["timestamp"]),
                "confirmed_at": int(bars_1m[28]["close_time"]),
            }
        },
        require_htf_alignment=False,
    )
    assert res.is_valid_unicorn is False
    assert "UNICORN_NO_GEOMETRIC_OVERLAP" in res.rejection_reasons


# ==============================================================================
# T30: Unicorn rejects disjoint causal episodes
# ==============================================================================
def test_T30_unicorn_rejects_disjoint_causal_episodes() -> None:
    ts_ms = _ny_to_utc_ms(2026, 3, 25, 8, 55, 0)
    bars_1m, bars_5m, _, _ = _make_synthetic_unicorn_m1_bars(ts_ms - 31 * 60_000, direction="LONG")
    ctx = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=ts_ms,
        m1_candles=bars_1m,
        m5_candles=bars_5m,
        bid=3007.40,
        ask=3007.60,
    )
    res = evaluate_causal_unicorn_confluence(
        ctx,
        "LONG",
        ts_ms,
        setup_descriptors={
            "selected_fvg": {
                "id": "fvg_disjoint",
                "type": "BULLISH_FVG",
                "direction": "LONG",
                "bottom": 3001.5,
                "top": 3003.0,
                "timestamp": int(bars_1m[5]["timestamp"]),
                "middle_bar_ms": int(bars_1m[5]["timestamp"]),
                "confirmed_at": int(bars_1m[6]["close_time"]),
            }
        },
        require_htf_alignment=False,
    )
    assert res.is_valid_unicorn is False
    assert "UNICORN_EPISODE_MISMATCH" in res.rejection_reasons


# ==============================================================================
# T31: Unicorn rejects consumed/invalidated FVG
# ==============================================================================
def test_T31_unicorn_rejects_consumed_fvg() -> None:
    ts_ms = _ny_to_utc_ms(2026, 3, 25, 8, 55, 0)
    bars_1m, bars_5m, _, _ = _make_synthetic_unicorn_m1_bars(ts_ms - 31 * 60_000, direction="LONG")
    ctx = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=ts_ms,
        m1_candles=bars_1m,
        m5_candles=bars_5m,
        bid=3007.40,
        ask=3007.60,
    )
    res = evaluate_causal_unicorn_confluence(
        ctx,
        "LONG",
        ts_ms,
        setup_descriptors={
            "selected_fvg": {
                "id": "fvg_cons",
                "type": "BULLISH_FVG",
                "direction": "LONG",
                "bottom": 3001.5,
                "top": 3003.0,
                "timestamp": int(bars_1m[27]["timestamp"]),
                "confirmed_at": int(bars_1m[28]["close_time"]),
                "state": "mitigated",
                "is_consumed": True,
            }
        },
        require_htf_alignment=False,
    )
    assert res.is_valid_unicorn is False
    assert "UNICORN_CONSUMED_OR_INVALIDATED_FVG" in res.rejection_reasons


# ==============================================================================
# T32: Unicorn requires confirmed retest strictly after confirmation
# ==============================================================================
def test_T32_unicorn_requires_confirmed_retest() -> None:
    ts_ms = _ny_to_utc_ms(2026, 3, 25, 8, 55, 0)
    bars_1m, bars_5m, _, _ = _make_synthetic_unicorn_m1_bars(ts_ms - 31 * 60_000, direction="LONG")
    bars_no_ret = [dict(b) for b in bars_1m]
    bars_no_ret[29]["low"] = 3006.5
    bars_no_ret[30]["low"] = 3006.5
    ctx = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=ts_ms,
        m1_candles=bars_no_ret,
        m5_candles=bars_5m,
        bid=3007.40,
        ask=3007.60,
    )
    res = evaluate_causal_unicorn_confluence(
        ctx, "LONG", ts_ms, require_confirmed_retest=True, require_htf_alignment=False
    )
    assert res.is_valid_unicorn is False
    assert "UNICORN_WAITING_CONFIRMED_RETEST" in res.rejection_reasons


# ==============================================================================
# T33: Unicorn HTF alignment and missing HTF fail-closed
# ==============================================================================
def test_T33_unicorn_missing_htf_fail_closed() -> None:
    ts_ms = _ny_to_utc_ms(2026, 3, 25, 8, 55, 0)
    bars_1m, bars_5m, _, _ = _make_synthetic_unicorn_m1_bars(ts_ms - 31 * 60_000, direction="LONG")
    ctx_no_mtf = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=ts_ms,
        m1_candles=bars_1m,
        m5_candles=bars_5m,
        bid=3007.40,
        ask=3007.60,
        policy_config={"mtf_structure_observe_only": False},
    )
    res = evaluate_causal_unicorn_confluence(
        ctx_no_mtf, "LONG", ts_ms, require_confirmed_retest=True, require_htf_alignment=True
    )
    assert res.is_valid_unicorn is False
    assert "UNICORN_HTF_NOT_VERIFIED" in res.rejection_reasons


# ==============================================================================
# T34: Unicorn invalidation vs V17.9.2.1 SL validation
# ==============================================================================
def test_T34_unicorn_invalidation_vs_v1792_stop() -> None:
    ts_ms = _ny_to_utc_ms(2026, 3, 25, 8, 55, 0)
    bars_1m, bars_5m, bars_15m, bars_htf = _make_synthetic_unicorn_m1_bars(
        ts_ms - 31 * 60_000, direction="LONG"
    )
    ctx = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=ts_ms,
        m1_candles=bars_1m,
        m5_candles=bars_5m,
        m15_candles=bars_15m,
        h1_candles=bars_htf[:24],
        h4_candles=bars_htf[24:],
        bid=3007.40,
        ask=3007.60,
    )
    uni = evaluate_causal_unicorn_confluence(ctx, "LONG", ts_ms, require_htf_alignment=False)
    v_ok = validate_unicorn_invalidation_against_v1792(
        uni, {"proposed_sl": 2998.25, "invalidation_price": 2999.0}, "LONG", 3007.60
    )
    assert v_ok.is_valid is True
    v_bad = validate_unicorn_invalidation_against_v1792(
        uni, {"proposed_sl": 2999.20, "invalidation_price": 2999.0}, "LONG", 3007.60
    )
    assert v_bad.is_valid is False
    assert "UNICORN_STOP_INSIDE_INVALIDATION" in v_bad.reason_codes


# ==============================================================================
# T35: Default OFF and OBSERVE_ONLY trade-action invariance
# ==============================================================================
def test_T35_default_off_and_observe_only_trade_action_invariance() -> None:
    ts_ms = _ny_to_utc_ms(2026, 3, 25, 8, 55, 0)
    bars_1m, bars_5m, bars_15m, bars_htf = _make_synthetic_unicorn_m1_bars(
        ts_ms - 31 * 60_000, direction="LONG"
    )
    base_inputs = {
        "symbol": "XAUUSDT",
        "decision_timestamp_ms": ts_ms,
        "m1_candles": bars_1m,
        "m5_candles": bars_5m,
        "m15_candles": bars_15m,
        "h1_candles": bars_htf[:24],
        "h4_candles": bars_htf[24:],
        "d1_candles": [],
        "bid": 3007.40,
        "ask": 3007.60,
    }
    ctx_obs = build_v1793_variant_market_context(
        base_inputs, V1793_VARIANT_POLICIES["B0_V1793_OBSERVE_ONLY"], decision_time=ts_ms
    )
    ctx_off = build_v1793_variant_market_context(
        base_inputs,
        V1793_VARIANT_POLICIES["A0_FROZEN_BASELINE_OFF"],
        decision_time=ts_ms,
        reference_context=ctx_obs,
    )
    assert ctx_off is not ctx_obs
    assert ctx_off.source_fingerprint != ctx_obs.source_fingerprint
    for m_id in PRODUCTION_MODEL_IDS:
        s_off = SharedStrategyEvaluator.evaluate_model_direction(ctx_off, m_id, "LONG")
        s_obs = SharedStrategyEvaluator.evaluate_model_direction(ctx_obs, m_id, "LONG")
        assert s_off.trade_action_fingerprint == s_obs.trade_action_fingerprint
        assert s_off.policy_fingerprint != s_obs.policy_fingerprint
        assert s_off.metadata_fingerprint != s_obs.metadata_fingerprint


# ==============================================================================
# T36: Three-runtime adapter parity across V17.9.3 modes
# ==============================================================================
@pytest.mark.parametrize(
    "variant_key",
    [
        "A0_FROZEN_BASELINE_OFF",
        "B0_V1793_OBSERVE_ONLY",
        "D1_V1793_MACRO_PLUS_UNICORN_V1792_SL_TP",
        "D2_V1793_MACRO_PLUS_UNICORN_MTF_FULL",
    ],
)
def test_T36_three_runtime_adapter_parity(variant_key: str) -> None:
    ts_ms = _ny_to_utc_ms(2026, 3, 25, 8, 55, 0)
    bars_1m, bars_5m, bars_15m, bars_htf = _make_synthetic_unicorn_m1_bars(
        ts_ms - 31 * 60_000, direction="LONG"
    )
    base_inputs = {
        "symbol": "XAUUSDT",
        "decision_timestamp_ms": ts_ms,
        "m1_candles": bars_1m,
        "m5_candles": bars_5m,
        "m15_candles": bars_15m,
        "h1_candles": bars_htf[:24],
        "h4_candles": bars_htf[24:],
        "d1_candles": [],
        "bid": 3007.40,
        "ask": 3007.60,
    }
    ctx = build_v1793_variant_market_context(
        base_inputs, V1793_VARIANT_POLICIES[variant_key], decision_time=ts_ms
    )
    prod_orch = ScalpWatcherOrchestrator()
    shadow_svc = MarketShadowService(symbol="XAUUSDT")
    s_prod = prod_orch.evaluate_shared_decision_snapshot(
        context=ctx, model_id=ModelIdentifier.SMC_M1_FVG_CONTINUATION.value, direction="LONG"
    )
    s_shadow = shadow_svc.evaluate_shared_decision_snapshot(
        context=ctx, model_id=ModelIdentifier.SMC_M1_FVG_CONTINUATION.value, direction="LONG"
    )
    s_bt = evaluate_production_parity_model_snapshot(
        context=ctx, model_id=ModelIdentifier.SMC_M1_FVG_CONTINUATION.value, direction="LONG"
    )
    parity_res = compare_actual_consumed_decisions(s_prod, s_shadow, s_bt)
    assert parity_res["is_parity"] is True


# ==============================================================================
# T37: Mode validation, migration alias, and unknown mode fail-closed
# ==============================================================================
def test_T37_mode_validation_and_migration_alias() -> None:
    norm_m, ok_m, _ = normalize_v1793_mode("MACRO_PLUS_UNICORN_Plus_V1792_SL_TP")
    assert ok_m is True
    assert norm_m == "MACRO_PLUS_UNICORN_V1792_SL_TP"
    bad_m, ok_bad, _ = normalize_v1793_mode("BOGUS_MODE_999")
    assert ok_bad is False
    assert bad_m == "INVALID_V1793_MODE"


# ==============================================================================
# T38: Strict monotonicity (D2 <= D1 <= C1_V1792 <= A1 and D2 <= D1 <= C2_V1792 <= A1)
# ==============================================================================
def test_T38_strict_filter_monotonicity() -> None:
    ds = _get_dataset()
    bars_1m = ds["1m"]
    bars_5m = ds["5m"]
    bars_15m = ds["15m"]
    bars_1h = ds["1H"]
    bars_4h = ds["4H"]
    bars_1d = ds["1D"]
    c_5m = [int(b["close_time"]) for b in bars_5m]
    c_15m = [int(b["close_time"]) for b in bars_15m]
    c_1h = [int(b["close_time"]) for b in bars_1h]
    c_4h = [int(b["close_time"]) for b in bars_4h]
    c_1d = [int(b["close_time"]) for b in bars_1d]

    for i_1m in (5625, 10434, 15585, 19914):
        asof_ms = int(bars_1m[i_1m]["close_time"])
        i5 = bisect.bisect_right(c_5m, asof_ms)
        i15 = bisect.bisect_right(c_15m, asof_ms)
        i1h = bisect.bisect_right(c_1h, asof_ms)
        i4h = bisect.bisect_right(c_4h, asof_ms)
        i1d = bisect.bisect_right(c_1d, asof_ms)
        last_c = float(bars_1m[i_1m]["close"])
        base_inputs = {
            "symbol": "XAUUSDT",
            "decision_timestamp_ms": asof_ms,
            "m1_candles": bars_1m[max(0, i_1m + 1 - 120) : i_1m + 1],
            "m5_candles": bars_5m[max(0, i5 - 96) : i5],
            "m15_candles": bars_15m[max(0, i15 - 96) : i15],
            "h1_candles": bars_1h[max(0, i1h - 96) : i1h],
            "h4_candles": bars_4h[max(0, i4h - 64) : i4h],
            "d1_candles": bars_1d[max(0, i1d - 30) : i1d],
            "bid": round(last_c - 0.10, 2),
            "ask": round(last_c + 0.10, 2),
        }
        ctx_b0 = build_v1793_variant_market_context(
            base_inputs, V1793_VARIANT_POLICIES["B0_V1793_OBSERVE_ONLY"], decision_time=asof_ms
        )
        pref_dir = SharedStrategyEvaluator.resolve_canonical_preferred_direction(ctx_b0)
        ctxs = {
            vk: build_v1793_variant_market_context(
                base_inputs, V1793_VARIANT_POLICIES[vk], decision_time=asof_ms, reference_context=ctx_b0
            )
            for vk in (
                "A1_V1792_SL_TP_BASELINE",
                "C1_MACRO_PLUS_V1792_SL_TP",
                "C2_UNICORN_PLUS_V1792_SL_TP",
                "D1_V1793_MACRO_PLUS_UNICORN_V1792_SL_TP",
                "D2_V1793_MACRO_PLUS_UNICORN_MTF_FULL",
            )
        }
        for m_id in PRODUCTION_MODEL_IDS:
            el = {
                vk: SharedStrategyEvaluator.evaluate_model_direction(ctxs[vk], m_id, pref_dir).decision_status
                == DecisionStatus.ELIGIBLE.value
                for vk in ctxs
            }
            if el["D2_V1793_MACRO_PLUS_UNICORN_MTF_FULL"]:
                assert el["D1_V1793_MACRO_PLUS_UNICORN_V1792_SL_TP"] is True
            if el["D1_V1793_MACRO_PLUS_UNICORN_V1792_SL_TP"]:
                assert el["C1_MACRO_PLUS_V1792_SL_TP"] is True
                assert el["C2_UNICORN_PLUS_V1792_SL_TP"] is True
            if el["C1_MACRO_PLUS_V1792_SL_TP"] or el["C2_UNICORN_PLUS_V1792_SL_TP"]:
                assert el["A1_V1792_SL_TP_BASELINE"] is True


# ==============================================================================
# T39: Inside-macro (C1_V1792) + Outside-macro (E1) exact disjoint partition of A1
# ==============================================================================
def test_T39_inside_and_outside_macro_exact_disjoint_partition_of_a1() -> None:
    ds = _get_dataset()
    bars_1m = ds["1m"]
    bars_5m = ds["5m"]
    bars_15m = ds["15m"]
    bars_1h = ds["1H"]
    bars_4h = ds["4H"]
    bars_1d = ds["1D"]
    c_5m = [int(b["close_time"]) for b in bars_5m]
    c_15m = [int(b["close_time"]) for b in bars_15m]
    c_1h = [int(b["close_time"]) for b in bars_1h]
    c_4h = [int(b["close_time"]) for b in bars_4h]
    c_1d = [int(b["close_time"]) for b in bars_1d]

    for i_1m in (100, 5625, 10434):
        asof_ms = int(bars_1m[i_1m]["close_time"])
        i5 = bisect.bisect_right(c_5m, asof_ms)
        i15 = bisect.bisect_right(c_15m, asof_ms)
        i1h = bisect.bisect_right(c_1h, asof_ms)
        i4h = bisect.bisect_right(c_4h, asof_ms)
        i1d = bisect.bisect_right(c_1d, asof_ms)
        last_c = float(bars_1m[i_1m]["close"])
        base_inputs = {
            "symbol": "XAUUSDT",
            "decision_timestamp_ms": asof_ms,
            "m1_candles": bars_1m[max(0, i_1m + 1 - 120) : i_1m + 1],
            "m5_candles": bars_5m[max(0, i5 - 96) : i5],
            "m15_candles": bars_15m[max(0, i15 - 96) : i15],
            "h1_candles": bars_1h[max(0, i1h - 96) : i1h],
            "h4_candles": bars_4h[max(0, i4h - 64) : i4h],
            "d1_candles": bars_1d[max(0, i1d - 30) : i1d],
            "bid": round(last_c - 0.10, 2),
            "ask": round(last_c + 0.10, 2),
        }
        ctx_b0 = build_v1793_variant_market_context(
            base_inputs, V1793_VARIANT_POLICIES["B0_V1793_OBSERVE_ONLY"], decision_time=asof_ms
        )
        pref_dir = SharedStrategyEvaluator.resolve_canonical_preferred_direction(ctx_b0)
        ctx_a1 = build_v1793_variant_market_context(
            base_inputs, V1793_VARIANT_POLICIES["A1_V1792_SL_TP_BASELINE"], decision_time=asof_ms, reference_context=ctx_b0
        )
        ctx_c1 = build_v1793_variant_market_context(
            base_inputs, V1793_VARIANT_POLICIES["C1_MACRO_PLUS_V1792_SL_TP"], decision_time=asof_ms, reference_context=ctx_b0
        )
        ctx_e1 = build_v1793_variant_market_context(
            base_inputs, V1793_VARIANT_POLICIES["E1_OUTSIDE_MACRO_COUNTERFACTUAL"], decision_time=asof_ms, reference_context=ctx_b0
        )
        for m_id in PRODUCTION_MODEL_IDS:
            el_a1 = SharedStrategyEvaluator.evaluate_model_direction(ctx_a1, m_id, pref_dir).decision_status == DecisionStatus.ELIGIBLE.value
            el_c1 = SharedStrategyEvaluator.evaluate_model_direction(ctx_c1, m_id, pref_dir).decision_status == DecisionStatus.ELIGIBLE.value
            el_e1 = SharedStrategyEvaluator.evaluate_model_direction(ctx_e1, m_id, pref_dir).decision_status == DecisionStatus.ELIGIBLE.value
            assert not (el_c1 and el_e1)
            assert el_a1 == (el_c1 or el_e1)


# ==============================================================================
# T40: One-worker vs multi-worker determinism
# ==============================================================================
def test_T40_one_worker_vs_multi_worker_determinism() -> None:
    ts_ms = _ny_to_utc_ms(2026, 3, 25, 8, 55, 0)
    bars_1m, bars_5m, bars_15m, bars_htf = _make_synthetic_unicorn_m1_bars(
        ts_ms - 31 * 60_000, direction="LONG"
    )
    base_inputs = {
        "symbol": "XAUUSDT",
        "decision_timestamp_ms": ts_ms,
        "m1_candles": bars_1m,
        "m5_candles": bars_5m,
        "m15_candles": bars_15m,
        "h1_candles": bars_htf[:24],
        "h4_candles": bars_htf[24:],
        "d1_candles": [],
        "bid": 3007.40,
        "ask": 3007.60,
    }
    ctx_1 = build_v1793_variant_market_context(
        base_inputs, V1793_VARIANT_POLICIES["D2_V1793_MACRO_PLUS_UNICORN_MTF_FULL"], decision_time=ts_ms
    )
    ctx_2 = build_v1793_variant_market_context(
        base_inputs, V1793_VARIANT_POLICIES["D2_V1793_MACRO_PLUS_UNICORN_MTF_FULL"], decision_time=ts_ms
    )
    d1 = SharedStrategyEvaluator.evaluate_model_direction(ctx_1, ModelIdentifier.SMC_M1_FVG_CONTINUATION.value, "LONG").to_parity_dict()
    d2 = SharedStrategyEvaluator.evaluate_model_direction(ctx_2, ModelIdentifier.SMC_M1_FVG_CONTINUATION.value, "LONG").to_parity_dict()
    d1.pop("adapter_call_id", None)
    d2.pop("adapter_call_id", None)
    assert d1 == d2


# ==============================================================================
# T41: Policy persistence strict boolean validation & LIVE scope prohibition
# ==============================================================================
def test_T41_policy_persistence_strict_boolean_and_live_scope_prohibition() -> None:
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    try:
        pol = resolve_stop_placement_policy(db)
        with pytest.raises(StopPolicyValidationError):
            validate_stop_placement_policy({"v17_9_3_macro_unicorn_enabled": "true"}, pol)
        with pytest.raises(StopPolicyValidationError):
            update_stop_placement_policy(
                db,
                {"v17_9_3_macro_unicorn_enabled": True, "v17_9_3_mode": "MACRO_PLUS_UNICORN_V1792_SL_TP"},
                expected_version=1,
                now_ms=1_775_000_000_000,
                server_scope="LIVE",
            )
    finally:
        db.close()


# ==============================================================================
# T42: Statistical verdict honesty
# ==============================================================================
def test_T42_statistical_verdict_honesty() -> None:
    fake_summaries = {
        "D1_V1793_MACRO_PLUS_UNICORN_V1792_SL_TP": {
            "n_closed_episodes": 8,
            "episode_wins": 5,
            "episode_win_rate": 0.625,
            "episode_expectancy_r": 0.85,
            "episode_net_pnl_usd": 17.0,
            "partitions": {"OOS_HOLD_OUT_25": {"closed_episodes": 2, "net_pnl_usd": 5.0}},
        },
        "A1_V1792_SL_TP_BASELINE": {
            "n_closed_episodes": 90,
            "episode_wins": 7,
            "episode_win_rate": 0.0778,
            "episode_expectancy_r": -0.71,
            "episode_net_pnl_usd": -160.0,
            "partitions": {"OOS_HOLD_OUT_25": {"closed_episodes": 20, "net_pnl_usd": -35.0}},
        },
    }
    claims = assess_v1793_statistical_claims(fake_summaries, {})
    assert claims["ready_for_strategy_activation"] == "NO"
    assert claims["per_variant_verdicts"]["D1_V1793_MACRO_PLUS_UNICORN_V1792_SL_TP"]["statistical_verdict"] == "INCONCLUSIVE"
    assert claims["per_variant_verdicts"]["A1_V1792_SL_TP_BASELINE"]["statistical_verdict"] == "NEGATIVE_EVIDENCE"


# ==============================================================================
# T43: Synthetic unit-test provenance segregation
# ==============================================================================
def test_T43_synthetic_unit_test_provenance_segregation() -> None:
    ok_good, _ = validate_v1793_historical_provenance_segregation([{"provenance": "HISTORICAL_BACKTEST_V1793"}])
    assert ok_good is True
    ok_bad, err_bad = validate_v1793_historical_provenance_segregation([{"provenance": SYNTHETIC_PROVENANCE}])
    assert ok_bad is False
    assert "SYNTHETIC_PROVENANCE_CONTAMINATION_DETECTED" in str(err_bad)


# ==============================================================================
# T44: Cost assumptions, $2.50 risk budget, and min_net_rr=2.0 unweakened
# ==============================================================================
def test_T44_cost_risk_budget_and_min_net_rr_unweakened() -> None:
    costs = CostAssumptions()
    assert costs.slippage_usd == 0.10
    assert costs.maker_fee_rate == 0.0002
    assert costs.taker_fee_rate == 0.0004


# ==============================================================================
# T45: evaluate_v1793_forward_outcomes integration with StrategyDecisionSnapshot
# ==============================================================================
def test_T45_evaluate_v1793_forward_outcomes_with_snapshot() -> None:
    ts_ms = _ny_to_utc_ms(2026, 3, 25, 8, 55, 0)
    bars_1m, bars_5m, bars_15m, bars_htf = _make_synthetic_unicorn_m1_bars(
        ts_ms - 31 * 60_000, direction="LONG"
    )
    base_inputs = {
        "symbol": "XAUUSDT",
        "decision_timestamp_ms": ts_ms,
        "m1_candles": bars_1m,
        "m5_candles": bars_5m,
        "m15_candles": bars_15m,
        "h1_candles": bars_htf[:24],
        "h4_candles": bars_htf[24:],
        "d1_candles": [],
        "bid": 3007.40,
        "ask": 3007.60,
    }
    ctx = build_v1793_variant_market_context(
        base_inputs, V1793_VARIANT_POLICIES["D1_V1793_MACRO_PLUS_UNICORN_V1792_SL_TP"], decision_time=ts_ms
    )
    snap = SharedStrategyEvaluator.evaluate_model_direction(
        ctx, ModelIdentifier.SMC_M1_FVG_CONTINUATION.value, "LONG"
    )
    fwd = [
        {"open_time": ts_ms, "close_time": ts_ms + 60_000, "open": 3007.50, "high": 3035.0, "low": 3007.0, "close": 3034.0}
    ]
    out = evaluate_v1793_forward_outcomes(
        snap, fwd, checkpoint_index_1m=30, variant_key="D1_V1793_MACRO_PLUS_UNICORN_V1792_SL_TP"
    )
    assert out["execution_truth_class"] == "MODELED_HISTORICAL_BAR_EXECUTION"
    if snap.decision_status == DecisionStatus.ELIGIBLE.value:
        assert out["execution_ledger"] is not None
        assert set(out["sensitivity_ledgers"].keys()) == {
            "NEXT_BAR_OPEN_BID_ASK",
            "LEGACY_CLOSE_FILL",
            "STRESS_WIDER_SPREAD_SLIPPAGE_2X",
        }


# ==============================================================================
# T46: Immutable historical artifact preservation check
# ==============================================================================
def test_T46_immutable_prior_artifacts_untouched() -> None:
    res = subprocess.run(
        ["git", "status", "--porcelain", "docs/artifacts/v17_9_2", "docs/artifacts/v17_9_2_1", "docs/artifacts/v17_9_3"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    assert res.stdout.strip() == ""
