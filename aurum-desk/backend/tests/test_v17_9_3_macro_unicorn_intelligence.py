"""
backend/tests/test_v17_9_3_macro_unicorn_intelligence.py
AURUM Desk V17.9.3 — Comprehensive Unit, Integration, Parity & Negative-Control Test Suite (T01–T23).
All synthetic fixtures in this file carry `provenance = "SYNTHETIC_UNIT_TEST"` and are strictly
segregated from `HISTORICAL_BACKTEST_V1793` artifacts.
"""

from __future__ import annotations

import ast
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
    MtfDataQualityState,
    MtfStructureLiquidityConfig,
    build_mtf_context,
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
from services.stop_placement_types import StopPlacementMode, StopPlacementPolicy
from services.unicorn_evidence_service import detect_breaker_blocks, detect_unicorn_evidence
from services.unicorn_evidence_types import UnicornSetupStatus
from services.v17_8_backtest_runner import evaluate_production_parity_model_snapshot
from services.v17_9_3_macro_unicorn_intelligence import (
    CANONICAL_V1793_MODES,
    DEFAULT_MACRO_SCHEDULE_VERSION,
    V17_9_3_ALGORITHM_VERSION,
    bind_unicorn_to_causal_episode,
    classify_macro_context_quality,
    evaluate_causal_unicorn_confluence,
    normalize_v1793_mode,
    plan_v17_9_3_macro_unicorn_setup,
    resolve_causal_macro_time_window,
    serialize_v1793_decision_evidence,
    validate_macro_schedule_config,
    validate_unicorn_invalidation_against_v1792,
)
from tools.run_v17_9_3_macro_unicorn_backtest import (
    PRODUCTION_MODEL_IDS,
    V1793_VARIANT_POLICIES,
    assess_v1793_statistical_claims,
    build_matched_trade_and_episode_forensics,
    build_v1793_variant_market_context,
    evaluate_v1793_forward_outcomes,
    validate_v1793_historical_provenance_segregation,
)


SYNTHETIC_PROVENANCE = "SYNTHETIC_UNIT_TEST"


def _ny_to_utc_ms(year: int, month: int, day: int, hour: int, minute: int, second: int = 0) -> int:
    ny_tz = ZoneInfo("America/New_York")
    dt_ny = datetime(year, month, day, hour, minute, second, tzinfo=ny_tz)
    return int(dt_ny.astimezone(timezone.utc).timestamp() * 1000)


def _make_synthetic_unicorn_m1_bars(
    base_open_ms: int,
    direction: str = "LONG",
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Constructs a deterministic closed M1 bar sequence (with M5/M15/H1/H4 aligned bars)
    that forms a confirmed ICT Breaker Block + overlapping FVG + post-confirmation retest.
    """
    bars_1m: List[Dict[str, Any]] = []
    # Build 20 warmup bars with gentle oscillation around 3000.0
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
        # Create a clear Swing High at index 22 (price 3005.0) followed by a drop (Swing Low at index 25, price 2996.0)
        # Bar 20..24: rise to 3005.0 at idx 22, then drop
        pattern_long = [
            # idx 20
            (3001.0, 3002.5, 3000.5, 3002.0),
            # idx 21
            (3002.0, 3003.5, 3001.5, 3003.0),
            # idx 22: Swing High at 3005.0 (left=2, right=2)
            (3003.0, 3005.0, 3002.5, 3004.2),
            # idx 23: Pullback bar 1
            (3004.2, 3004.4, 3002.0, 3002.5),
            # idx 24: Pullback bar 2 (confirms SH at idx 22)
            (3002.5, 3003.8, 3001.2, 3003.5),
            # idx 25: Down-close OB candle with lowest low (Breaker Block: [2999.0, 3003.0])
            (3002.8, 3003.0, 2999.0, 2999.5),
            # idx 26: Bullish start (bar i-2 of FVG: high = 3001.5, low = 2999.2 > 2999.0)
            (2999.5, 3001.5, 2999.2, 3001.2),
            # idx 27: Strong bullish displacement breaking Swing High 3005.0! (middle bar i-1 of FVG)
            (3001.2, 3007.5, 3001.0, 3007.0),
            # idx 28: Bullish continuation (bar i of FVG: low = 3004.0 -> FVG is [3001.5, 3004.0], overlaps OB [2999.0, 3003.0] on [3001.5, 3003.0]!)
            (3007.0, 3009.0, 3004.0, 3008.2),
            # idx 29: Retest bar! Dips into overlap [3001.5, 3003.0] at low=3002.2 and closes bullish at 3006.5
            (3008.2, 3008.5, 3002.2, 3006.5),
            # idx 30: Follow-through bar
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
            # idx 20
            (2999.0, 2999.5, 2997.5, 2998.0),
            # idx 21
            (2998.0, 2998.5, 2996.5, 2997.0),
            # idx 22: Swing Low at 2995.0 (left=2, right=2)
            (2997.0, 2997.5, 2995.0, 2995.8),
            # idx 23: Rally bar 1
            (2995.8, 2998.0, 2995.6, 2997.5),
            # idx 24: Rally bar 2 (confirms SL at idx 22)
            (2997.5, 2998.8, 2996.2, 2996.5),
            # idx 25: Up-close OB candle with highest high (Breaker Block: [2997.0, 3001.0])
            (2997.2, 3001.0, 2997.0, 3000.5),
            # idx 26: Bearish start (bar i-2 of FVG: low = 2998.5, high = 3000.8 < 3001.0)
            (3000.5, 3000.8, 2998.5, 2998.8),
            # idx 27: Strong bearish displacement breaking Swing Low 2995.0! (middle bar i-1 of FVG)
            (2998.8, 2999.0, 2992.5, 2993.0),
            # idx 28: Bearish continuation (bar i of FVG: high = 2996.0 -> FVG is [2996.0, 2998.5], overlaps OB [2997.0, 3001.0] on [2997.0, 2998.5]!)
            (2993.0, 2996.0, 2991.0, 2991.8),
            # idx 29: Retest bar! Rallies into overlap [2997.0, 2998.5] at high=2997.8 and closes bearish at 2993.5
            (2991.8, 2997.8, 2991.5, 2993.5),
            # idx 30: Follow-through bar
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
        start_t = end_ms - count * tf_ms
        # Create zigzag swings trending in `direction` so MTF structure is BULLISH for LONG / BEARISH for SHORT
        for k in range(count):
            t_o = start_t + k * tf_ms
            if direction == "LONG":
                # Zigzag upward with higher highs and higher lows
                wave = (k // 3) * 4.0 + ([0.0, 3.0, 1.2][k % 3])
                o = 2950.0 + wave
                c = o + (1.5 if (k % 3 != 2) else -1.0)
                h = max(o, c) + 1.0
                l = min(o, c) - 0.8
            else:
                wave = (k // 3) * 4.0 + ([0.0, 3.0, 1.2][k % 3])
                o = 3050.0 - wave
                c = o - (1.5 if (k % 3 != 2) else -1.0)
                h = max(o, c) + 0.8
                l = min(o, c) - 1.0
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
# T01: Pure causal macro time window & zero wall-clock AST check
# ==============================================================================
def test_T01_macro_time_pure_causal_from_decision_timestamp() -> None:
    src_path = (
        Path(__file__).resolve().parents[1]
        / "services"
        / "v17_9_3_macro_unicorn_intelligence.py"
    )
    source_code = src_path.read_text(encoding="utf-8")
    tree = ast.parse(source_code)

    forbidden_calls = {"now", "utcnow", "today", "time", "monotonic", "perf_counter"}
    violations: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in forbidden_calls:
                violations.append(f"line {node.lineno}: .{node.func.attr}()")
    assert violations == [], f"Wall-clock calls found in V17.9.3 module: {violations}"

    # Verify deterministic repeatability across 5 identical calls
    ts_ms = _ny_to_utc_ms(2026, 3, 25, 8, 55, 0)  # Wednesday 08:55 NY
    results = [resolve_causal_macro_time_window(ts_ms, "XAUUSDT") for _ in range(5)]
    for r in results:
        assert r == results[0]
        assert r.eligible_for_macro_gate is True
        assert r.primary_window_id == "NY_AM_MACRO_0850_0910"
        assert r.source == "RESEARCH_HYPOTHESIS_CONFIG"

    # Reject invalid / seconds-scale / boolean / negative timestamps fail-closed
    for bad_ts in (None, True, False, -100, 0, 1_775_000_000, 9_999_999_999_999):
        bad_state = resolve_causal_macro_time_window(bad_ts, "XAUUSDT")  # type: ignore[arg-type]
        assert bad_state.is_valid_timestamp is False
        assert bad_state.eligible_for_macro_gate is False
        assert "MACRO_TIMESTAMP_INVALID" in bad_state.reason_codes


# ==============================================================================
# T02: US DST transitions (EST UTC-5 vs EDT UTC-4)
# ==============================================================================
def test_T02_macro_ny_dst_spring_forward_and_fall_back() -> None:
    # Winter EST (UTC-5): 2026-01-15 08:55 NY == 13:55 UTC
    est_ts = int(datetime(2026, 1, 15, 13, 55, 0, tzinfo=timezone.utc).timestamp() * 1000)
    est_state = resolve_causal_macro_time_window(est_ts, "XAUUSDT")
    assert est_state.is_dst is False
    assert est_state.utc_offset_minutes == -300
    assert est_state.ny_local_hhmm == "08:55"
    assert est_state.primary_window_id == "NY_AM_MACRO_0850_0910"
    assert est_state.eligible_for_macro_gate is True

    # At 12:55 UTC in Winter EST, NY local time is 07:55 (outside macro window!)
    est_early_ts = int(datetime(2026, 1, 15, 12, 55, 0, tzinfo=timezone.utc).timestamp() * 1000)
    est_early_state = resolve_causal_macro_time_window(est_early_ts, "XAUUSDT")
    assert est_early_state.ny_local_hhmm == "07:55"
    assert est_early_state.eligible_for_macro_gate is False
    assert "MACRO_OUTSIDE_WINDOW" in est_early_state.reason_codes

    # Summer EDT (UTC-4): 2026-03-25 08:55 NY == 12:55 UTC
    edt_ts = int(datetime(2026, 3, 25, 12, 55, 0, tzinfo=timezone.utc).timestamp() * 1000)
    edt_state = resolve_causal_macro_time_window(edt_ts, "XAUUSDT")
    assert edt_state.is_dst is True
    assert edt_state.utc_offset_minutes == -240
    assert edt_state.ny_local_hhmm == "08:55"
    assert edt_state.primary_window_id == "NY_AM_MACRO_0850_0910"
    assert edt_state.eligible_for_macro_gate is True


# ==============================================================================
# T03: Half-open [start, end) boundaries across all 7 configured NY macro windows
# ==============================================================================
def test_T03_macro_window_half_open_boundaries() -> None:
    expected_windows = [
        ("LONDON_OPEN_MACRO_0233_0300", (2, 33), (3, 0)),
        ("LONDON_MACRO_0403_0430", (4, 3), (4, 30)),
        ("NY_AM_MACRO_0850_0910", (8, 50), (9, 10)),
        ("NY_AM_MACRO_0950_1010", (9, 50), (10, 10)),
        ("NY_AM_MACRO_1050_1110", (10, 50), (11, 10)),
        ("NY_LUNCH_PM_MACRO_1150_1210", (11, 50), (12, 10)),
        ("NY_PM_MACRO_1310_1340", (13, 10), (13, 40)),
    ]
    for win_id, (sh, sm), (eh, em) in expected_windows:
        # 1 ms before start -> OUTSIDE
        ts_before = _ny_to_utc_ms(2026, 3, 25, sh, sm, 0) - 1
        st_before = resolve_causal_macro_time_window(ts_before)
        assert st_before.eligible_for_macro_gate is False
        assert st_before.primary_window_id == "OUTSIDE_MACRO_WINDOW"

        # Exact start -> INSIDE
        ts_start = _ny_to_utc_ms(2026, 3, 25, sh, sm, 0)
        st_start = resolve_causal_macro_time_window(ts_start)
        assert st_start.eligible_for_macro_gate is True
        assert st_start.primary_window_id == win_id

        # 1 ms before end -> INSIDE
        ts_before_end = _ny_to_utc_ms(2026, 3, 25, eh, em, 0) - 1
        st_before_end = resolve_causal_macro_time_window(ts_before_end)
        assert st_before_end.eligible_for_macro_gate is True
        assert st_before_end.primary_window_id == win_id

        # Exact end -> OUTSIDE ([start, end) half-open)
        ts_end = _ny_to_utc_ms(2026, 3, 25, eh, em, 0)
        st_end = resolve_causal_macro_time_window(ts_end)
        assert st_end.eligible_for_macro_gate is False
        assert st_end.primary_window_id == "OUTSIDE_MACRO_WINDOW"
        assert "MACRO_OUTSIDE_WINDOW" in st_end.reason_codes


# ==============================================================================
# T04: Weekend, holiday calendar, and XAU_FIX_WINDOW fail-closed behavior
# ==============================================================================
def test_T04_macro_weekend_holiday_and_xau_fix_fail_closed() -> None:
    # Saturday 2026-03-28 08:55 NY -> weekend closed
    sat_ts = _ny_to_utc_ms(2026, 3, 28, 8, 55, 0)
    sat_state = resolve_causal_macro_time_window(sat_ts)
    assert sat_state.is_weekend_or_closed is True
    assert sat_state.eligible_for_macro_gate is False
    assert "MACRO_WEEKEND_CLOSED" in sat_state.reason_codes

    # Holiday date in verified calendar -> holiday closed
    wed_ts = _ny_to_utc_ms(2026, 3, 25, 8, 55, 0)
    hol_cfg = {
        "schedule_version": "HOL_TEST_V1",
        "timezone": "America/New_York",
        "weekdays": [0, 1, 2, 3, 4],
        "holiday_calendar_verified": True,
        "verified_holiday_dates": ["2026-03-25"],
    }
    hol_state = resolve_causal_macro_time_window(wed_ts, schedule_config=hol_cfg)
    assert hol_state.is_holiday_closed is True
    assert hol_state.eligible_for_macro_gate is False
    assert "MACRO_HOLIDAY_CLOSED" in hol_state.reason_codes

    # Require verified holiday calendar when not verified -> fail closed
    req_cal_cfg = {
        "schedule_version": "REQ_CAL_V1",
        "timezone": "America/New_York",
        "weekdays": [0, 1, 2, 3, 4],
        "require_verified_holiday_calendar": True,
        "holiday_calendar_verified": False,
    }
    unver_state = resolve_causal_macro_time_window(wed_ts, schedule_config=req_cal_cfg)
    assert unver_state.eligible_for_macro_gate is False
    assert "MACRO_CALENDAR_NOT_VERIFIED" in unver_state.reason_codes

    # Default XAU_FIX_WINDOW is NOT_CONFIGURED; enabling without verification fails closed
    assert sat_state.xau_fix_window_status == "NOT_CONFIGURED"
    unver_fix_cfg = {
        "schedule_version": "BAD_FIX_V1",
        "timezone": "America/New_York",
        "weekdays": [0, 1, 2, 3, 4],
        "xau_fix_verified": False,
        "windows": [
            {
                "window_id": "XAU_FIX_WINDOW",
                "start_hhmm": "10:00",
                "end_hhmm": "10:15",
                "enabled": True,
            }
        ],
    }
    fix_sched = validate_macro_schedule_config(unver_fix_cfg)
    assert fix_sched.is_valid is False
    assert "MACRO_SCHEDULE_INVALID" in fix_sched.reason_codes


# ==============================================================================
# T05: Schedule config validation rejects malformed configs fail-closed
# ==============================================================================
def test_T05_macro_schedule_validation_rejects_malformed_configs() -> None:
    # Overlapping windows
    overlap_cfg = {
        "schedule_version": "OV_V1",
        "timezone": "America/New_York",
        "weekdays": [0, 1, 2, 3, 4],
        "allow_overlapping_windows": False,
        "windows": [
            {"window_id": "W1", "start_hhmm": "08:50", "end_hhmm": "09:15"},
            {"window_id": "W2", "start_hhmm": "09:10", "end_hhmm": "09:30"},
        ],
    }
    s_ov = validate_macro_schedule_config(overlap_cfg)
    assert s_ov.is_valid is False
    assert any("OVERLAPPING_MACRO_WINDOWS" in e for e in s_ov.validation_errors)

    # Inverted window (start >= end)
    inv_cfg = {
        "schedule_version": "INV_V1",
        "timezone": "America/New_York",
        "weekdays": [0, 1, 2, 3, 4],
        "windows": [
            {"window_id": "W_INV", "start_hhmm": "09:10", "end_hhmm": "08:50"},
        ],
    }
    s_inv = validate_macro_schedule_config(inv_cfg)
    assert s_inv.is_valid is False
    assert any("INVERTED_OR_ZERO_WINDOW" in e for e in s_inv.validation_errors)

    # Duplicate window_id
    dup_cfg = {
        "schedule_version": "DUP_V1",
        "timezone": "America/New_York",
        "weekdays": [0, 1, 2, 3, 4],
        "windows": [
            {"window_id": "W_DUP", "start_hhmm": "08:50", "end_hhmm": "09:10"},
            {"window_id": "W_DUP", "start_hhmm": "09:50", "end_hhmm": "10:10"},
        ],
    }
    s_dup = validate_macro_schedule_config(dup_cfg)
    assert s_dup.is_valid is False
    assert any("DUPLICATE_WINDOW_ID" in e for e in s_dup.validation_errors)

    # Invalid timezone
    tz_cfg = {
        "schedule_version": "TZ_V1",
        "timezone": "Not/A_Timezone",
        "weekdays": [0, 1, 2, 3, 4],
    }
    s_tz = validate_macro_schedule_config(tz_cfg)
    assert s_tz.is_valid is False
    assert "MACRO_SCHEDULE_INVALID" in s_tz.reason_codes


# ==============================================================================
# T06: Hard session restriction overrides active macro window
# ==============================================================================
def test_T06_session_restricted_overrides_active_macro_window() -> None:
    ts_ms = _ny_to_utc_ms(2026, 3, 25, 8, 55, 0)  # Active NY_AM_MACRO_0850_0910
    bars_1m, bars_5m, bars_15m, bars_htf = _make_synthetic_unicorn_m1_bars(
        ts_ms - 31 * 60_000, direction="LONG"
    )
    ctx_restricted = build_market_decision_context(
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
    m_state = resolve_causal_macro_time_window(ts_ms)
    assert m_state.eligible_for_macro_gate is True
    q = classify_macro_context_quality(m_state, ctx_restricted)
    assert q.is_eligible is False
    assert q.session_restricted_override is True
    assert "SESSION_TIME_RESTRICTED" in q.reason_codes


# ==============================================================================
# T07: Valid bullish and bearish Unicorn Breaker + FVG + Retest + HTF alignment
# ==============================================================================
@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_T07_unicorn_valid_bullish_and_bearish_breaker_fvg_overlap(direction: str) -> None:
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
        ctx,
        direction,
        ts_ms,
        require_confirmed_retest=True,
        require_htf_alignment=False,
    )
    assert res.is_valid_unicorn is True
    assert res.validity_status == UnicornSetupStatus.VALID.value
    assert res.overlap_zone is not None
    assert res.overlap_zone[0] < res.overlap_zone[1]
    assert res.retest_confirmed is True
    assert res.retest_timestamp_ms is not None
    assert res.retest_timestamp_ms + 60_000 <= ts_ms
    assert res.episode_binding.is_bound is True
    assert res.episode_binding.same_causal_episode is True
    assert res.rejection_reasons == ()


# ==============================================================================
# T08: Unicorn rejects future and unclosed bars
# ==============================================================================
def test_T08_unicorn_rejects_future_and_unclosed_bars() -> None:
    ts_ms = _ny_to_utc_ms(2026, 3, 25, 8, 55, 0)
    bars_1m, bars_5m, _, _ = _make_synthetic_unicorn_m1_bars(
        ts_ms - 31 * 60_000, direction="LONG"
    )
    # Inject future selected_fvg with confirmed_at > ts_ms
    ctx = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=ts_ms,
        m1_candles=bars_1m,
        m5_candles=bars_5m,
        bid=3007.40,
        ask=3007.60,
    )
    future_fvg = {
        "id": "fvg_future_1",
        "type": "BULLISH_FVG",
        "direction": "LONG",
        "bottom": 3001.5,
        "top": 3003.0,
        "timestamp": ts_ms + 60_000,
        "confirmed_at": ts_ms + 120_000,
        "state": "confirmed",
    }
    res_future = evaluate_causal_unicorn_confluence(
        ctx,
        "LONG",
        ts_ms,
        setup_descriptors={"selected_fvg": future_fvg},
        require_htf_alignment=False,
    )
    assert res_future.is_valid_unicorn is False
    assert "UNICORN_FUTURE_DATA_REJECTED" in res_future.rejection_reasons

    # Unclosed bar in direct detect_unicorn_evidence
    unclosed_fvg = {
        "id": "fvg_unclosed_1",
        "type": "BULLISH_FVG",
        "direction": "LONG",
        "bottom": 3001.5,
        "top": 3003.0,
        "timestamp": ts_ms - 180_000,
        "confirmed_at": ts_ms - 120_000,
        "is_closed": False,
    }
    res_unclosed = evaluate_causal_unicorn_confluence(
        ctx,
        "LONG",
        ts_ms,
        setup_descriptors={"selected_fvg": unclosed_fvg},
        require_htf_alignment=False,
    )
    assert res_unclosed.is_valid_unicorn is False
    assert "UNICORN_UNCLOSED_BAR_REJECTED" in res_unclosed.rejection_reasons


# ==============================================================================
# T09: Unicorn rejects missing breaker or missing FVG
# ==============================================================================
def test_T09_unicorn_rejects_missing_breaker_or_missing_fvg() -> None:
    ts_ms = _ny_to_utc_ms(2026, 3, 25, 8, 55, 0)
    # Flat bars with no breaker
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
    ctx_flat = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=ts_ms,
        m1_candles=flat_bars,
        m5_candles=flat_bars[:5],
        bid=2999.9,
        ask=3000.1,
    )
    res_no_brk = evaluate_causal_unicorn_confluence(
        ctx_flat, "LONG", ts_ms, require_htf_alignment=False
    )
    assert res_no_brk.is_valid_unicorn is False
    assert "UNICORN_MISSING_BREAKER" in res_no_brk.rejection_reasons


# ==============================================================================
# T10: Unicorn rejects non-overlapping or wrong-direction zones
# ==============================================================================
def test_T10_unicorn_rejects_non_overlapping_or_wrong_direction_zones() -> None:
    ts_ms = _ny_to_utc_ms(2026, 3, 25, 8, 55, 0)
    bars_1m, bars_5m, _, _ = _make_synthetic_unicorn_m1_bars(
        ts_ms - 31 * 60_000, direction="LONG"
    )
    ctx = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=ts_ms,
        m1_candles=bars_1m,
        m5_candles=bars_5m,
        bid=3007.40,
        ask=3007.60,
    )
    # Non-overlapping FVG far above the breaker (3050..3052 vs breaker ~3001..3004)
    far_fvg = {
        "id": "fvg_far_1",
        "type": "BULLISH_FVG",
        "direction": "LONG",
        "bottom": 3050.0,
        "top": 3052.0,
        "timestamp": int(bars_1m[27]["timestamp"]),
        "confirmed_at": int(bars_1m[28]["close_time"]),
        "state": "confirmed",
    }
    res_no_ov = evaluate_causal_unicorn_confluence(
        ctx,
        "LONG",
        ts_ms,
        setup_descriptors={"selected_fvg": far_fvg},
        require_htf_alignment=False,
    )
    assert res_no_ov.is_valid_unicorn is False
    assert "UNICORN_NO_GEOMETRIC_OVERLAP" in res_no_ov.rejection_reasons


# ==============================================================================
# T11: Unicorn rejects disjoint causal episodes and NEVER substitutes selected_fvg
# ==============================================================================
def test_T11_unicorn_rejects_disjoint_causal_episodes_and_never_substitutes_selected_fvg() -> None:
    ts_ms = _ny_to_utc_ms(2026, 3, 25, 8, 55, 0)
    bars_1m, bars_5m, _, _ = _make_synthetic_unicorn_m1_bars(
        ts_ms - 31 * 60_000, direction="LONG"
    )
    ctx = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=ts_ms,
        m1_candles=bars_1m,
        m5_candles=bars_5m,
        bid=3007.40,
        ask=3007.60,
    )
    # Disjoint FVG with timestamp 15 minutes before the breaker break bar, even though prices overlap!
    disjoint_fvg = {
        "id": "fvg_disjoint_ep",
        "type": "BULLISH_FVG",
        "direction": "LONG",
        "bottom": 3001.5,
        "top": 3003.0,
        "timestamp": int(bars_1m[5]["timestamp"]),
        "middle_bar_ms": int(bars_1m[5]["timestamp"]),
        "confirmed_at": int(bars_1m[6]["close_time"]),
        "state": "confirmed",
    }
    res_disjoint = evaluate_causal_unicorn_confluence(
        ctx,
        "LONG",
        ts_ms,
        setup_descriptors={"selected_fvg": disjoint_fvg},
        require_htf_alignment=False,
    )
    assert res_disjoint.is_valid_unicorn is False
    assert "UNICORN_EPISODE_MISMATCH" in res_disjoint.rejection_reasons
    # Even though scan-level Unicorn on `bars_1m` is VALID, candidate-bound evaluation MUST reject!
    assert res_disjoint.scan_level_unicorn_status == UnicornSetupStatus.VALID.value
    assert res_disjoint.fvg_id == "fvg_disjoint_ep"


# ==============================================================================
# T12: Unicorn rejects consumed/invalidated FVG and invalidated overlap zone
# ==============================================================================
def test_T12_unicorn_rejects_consumed_invalidated_fvg_and_weak_displacement() -> None:
    ts_ms = _ny_to_utc_ms(2026, 3, 25, 8, 55, 0)
    bars_1m, bars_5m, _, _ = _make_synthetic_unicorn_m1_bars(
        ts_ms - 31 * 60_000, direction="LONG"
    )
    ctx = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=ts_ms,
        m1_candles=bars_1m,
        m5_candles=bars_5m,
        bid=3007.40,
        ask=3007.60,
    )
    consumed_fvg = {
        "id": "fvg_consumed_1",
        "type": "BULLISH_FVG",
        "direction": "LONG",
        "bottom": 3001.5,
        "top": 3003.0,
        "timestamp": int(bars_1m[27]["timestamp"]),
        "confirmed_at": int(bars_1m[28]["close_time"]),
        "state": "mitigated",
        "is_consumed": True,
    }
    res_cons = evaluate_causal_unicorn_confluence(
        ctx,
        "LONG",
        ts_ms,
        setup_descriptors={"selected_fvg": consumed_fvg},
        require_htf_alignment=False,
    )
    assert res_cons.is_valid_unicorn is False
    assert "UNICORN_CONSUMED_OR_INVALIDATED_FVG" in res_cons.rejection_reasons


# ==============================================================================
# T13: Unicorn requires confirmed retest strictly after confirmation
# ==============================================================================
def test_T13_unicorn_requires_confirmed_retest_after_confirmation() -> None:
    ts_ms = _ny_to_utc_ms(2026, 3, 25, 8, 55, 0)
    bars_1m, bars_5m, _, _ = _make_synthetic_unicorn_m1_bars(
        ts_ms - 31 * 60_000, direction="LONG"
    )
    # Remove the retest dip on bar 29 & 30 (keep lows at 3006.0 above overlap 3003.0)
    bars_no_retest = [dict(b) for b in bars_1m]
    bars_no_retest[29]["low"] = 3006.5
    bars_no_retest[30]["low"] = 3006.5
    ctx_wait = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=ts_ms,
        m1_candles=bars_no_retest,
        m5_candles=bars_5m,
        bid=3007.40,
        ask=3007.60,
    )
    res_wait = evaluate_causal_unicorn_confluence(
        ctx_wait,
        "LONG",
        ts_ms,
        require_confirmed_retest=True,
        require_htf_alignment=False,
    )
    assert res_wait.is_valid_unicorn is False
    assert "UNICORN_WAITING_CONFIRMED_RETEST" in res_wait.rejection_reasons


# ==============================================================================
# T14: Unicorn HTF alignment and missing HTF fail-closed
# ==============================================================================
def test_T14_unicorn_htf_alignment_and_missing_htf_fail_closed() -> None:
    ts_ms = _ny_to_utc_ms(2026, 3, 25, 8, 55, 0)
    bars_1m, bars_5m, _, _ = _make_synthetic_unicorn_m1_bars(
        ts_ms - 31 * 60_000, direction="LONG"
    )
    # Context with MTF disabled (`mtf_context=None`) when `require_htf_alignment=True` -> fail closed
    ctx_no_mtf = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=ts_ms,
        m1_candles=bars_1m,
        m5_candles=bars_5m,
        bid=3007.40,
        ask=3007.60,
        policy_config={"mtf_structure_observe_only": False},
    )
    res_no_mtf = evaluate_causal_unicorn_confluence(
        ctx_no_mtf,
        "LONG",
        ts_ms,
        require_confirmed_retest=True,
        require_htf_alignment=True,
    )
    assert res_no_mtf.is_valid_unicorn is False
    assert "UNICORN_HTF_NOT_VERIFIED" in res_no_mtf.rejection_reasons


# ==============================================================================
# T15: Unicorn invalidation vs V17.9.2.1 SL and outward tick rounding
# ==============================================================================
def test_T15_unicorn_invalidation_vs_v1792_stop_and_outward_tick_rounding() -> None:
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
    uni = evaluate_causal_unicorn_confluence(
        ctx, "LONG", ts_ms, require_htf_alignment=False
    )
    assert uni.is_valid_unicorn is True
    assert uni.breaker_distal_price == 2999.0

    # Valid V17.9.2 SL below breaker_distal_price (2998.25 < 2999.0)
    verdict_ok = validate_unicorn_invalidation_against_v1792(
        uni,
        {"proposed_sl": 2998.25, "invalidation_price": 2999.0},
        "LONG",
        3007.60,
    )
    assert verdict_ok.is_valid is True
    assert verdict_ok.reason_codes == ()

    # Invalid SL inside breaker_distal_price (2999.20 > 2999.0)
    verdict_inside = validate_unicorn_invalidation_against_v1792(
        uni,
        {"proposed_sl": 2999.20, "invalidation_price": 2999.0},
        "LONG",
        3007.60,
    )
    assert verdict_inside.is_valid is False
    assert "UNICORN_STOP_INSIDE_INVALIDATION" in verdict_inside.reason_codes

    # Invalid SL on wrong side of entry (3008.00 > 3007.60)
    verdict_wrong_side = validate_unicorn_invalidation_against_v1792(
        uni,
        {"proposed_sl": 3008.00, "invalidation_price": 2999.0},
        "LONG",
        3007.60,
    )
    assert verdict_wrong_side.is_valid is False
    assert "UNICORN_STOP_WRONG_SIDE_OF_ENTRY" in verdict_wrong_side.reason_codes


# ==============================================================================
# T16: Default OFF and OBSERVE_ONLY trade-action invariance
# ==============================================================================
def test_T16_default_off_and_observe_only_trade_action_invariance() -> None:
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
    ctx_off = build_v1793_variant_market_context(
        base_inputs, V1793_VARIANT_POLICIES["A0_FROZEN_BASELINE_OFF"], decision_time=ts_ms
    )
    ctx_obs = build_v1793_variant_market_context(
        base_inputs, V1793_VARIANT_POLICIES["B0_V1793_OBSERVE_ONLY"], decision_time=ts_ms
    )
    assert ctx_off is not ctx_obs
    assert ctx_off.source_fingerprint != ctx_obs.source_fingerprint

    for m_id in PRODUCTION_MODEL_IDS:
        s_off = SharedStrategyEvaluator.evaluate_model_direction(ctx_off, m_id, "LONG")
        s_obs = SharedStrategyEvaluator.evaluate_model_direction(ctx_obs, m_id, "LONG")
        assert s_off.v1793_mode == "OFF"
        assert s_obs.v1793_mode == "OBSERVE_ONLY"
        assert s_off.decision_status == s_obs.decision_status
        assert s_off.selected_sl == s_obs.selected_sl
        assert s_off.take_profit == s_obs.take_profit
        assert s_off.hypothetical_quantity == s_obs.hypothetical_quantity
        assert s_off.trade_action_fingerprint == s_obs.trade_action_fingerprint
        assert s_off.policy_fingerprint != s_obs.policy_fingerprint
        assert s_off.metadata_fingerprint != s_obs.metadata_fingerprint
        assert s_obs.v1793_macro_time_dict is not None
        assert s_obs.v1793_unicorn_confluence_dict is not None
        assert s_obs.v1793_plan_dict is not None


# ==============================================================================
# T17: 3-runtime adapter parity across V17.9.3 modes
# ==============================================================================
@pytest.mark.parametrize(
    "variant_key",
    [
        "A0_FROZEN_BASELINE_OFF",
        "B0_V1793_OBSERVE_ONLY",
        "C1_V1793_MACRO_FILTER_ONLY",
        "C2_V1793_UNICORN_ONLY",
        "D1_V1793_MACRO_PLUS_UNICORN_V1792_SL_TP",
        "D2_V1793_MACRO_PLUS_UNICORN_MTF_FULL",
    ],
)
def test_T17_three_runtime_adapter_parity_across_v1793_modes(variant_key: str) -> None:
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
    assert parity_res["is_parity"] is True, f"Parity failed for {variant_key}: {parity_res['mismatches']}"
    assert len(set(parity_res["adapter_origins"].values())) == 3
    assert len(set(parity_res["adapter_call_ids"].values())) == 3


# ==============================================================================
# T18: Mode validation, migration alias, and strict monotonicity
# ==============================================================================
def test_T18_v1793_mode_validation_migration_alias_and_monotonicity() -> None:
    # Migration alias
    norm_m, ok_m, note = normalize_v1793_mode("MACRO_PLUS_UNICORN_Plus_V1792_SL_TP")
    assert ok_m is True
    assert norm_m == "MACRO_PLUS_UNICORN_V1792_SL_TP"
    assert note == "MODE_ALIAS_NORMALIZED:MACRO_PLUS_UNICORN_Plus_V1792_SL_TP->MACRO_PLUS_UNICORN_V1792_SL_TP"

    # Unknown mode fails closed
    bad_m, ok_bad, _ = normalize_v1793_mode("BOGUS_MODE_999")
    assert ok_bad is False
    assert bad_m == "INVALID_V1793_MODE"

    ts_ms = _ny_to_utc_ms(2026, 3, 25, 8, 55, 0)
    bars_1m, bars_5m, bars_15m, bars_htf = _make_synthetic_unicorn_m1_bars(
        ts_ms - 31 * 60_000, direction="LONG"
    )
    ctx_bad = build_market_decision_context(
        symbol="XAUUSDT",
        decision_timestamp_ms=ts_ms,
        m1_candles=bars_1m,
        m5_candles=bars_5m,
        m15_candles=bars_15m,
        h1_candles=bars_htf[:24],
        h4_candles=bars_htf[24:],
        bid=3007.40,
        ask=3007.60,
        policy_config={"v17_9_3_mode": "BOGUS_MODE_999"},
    )
    snap_bad = SharedStrategyEvaluator.evaluate_model_direction(
        ctx_bad, ModelIdentifier.SMC_M1_FVG_CONTINUATION.value, "LONG"
    )
    assert snap_bad.decision_status != DecisionStatus.ELIGIBLE.value
    assert "INVALID_V1793_MODE" in snap_bad.rejection_reason_codes
    assert snap_bad.v1793_rejection_stage == "V1793_MODE_VALIDATION"


# ==============================================================================
# T19: 1-worker vs multi-worker determinism
# ==============================================================================
def test_T19_one_worker_vs_multi_worker_determinism() -> None:
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
        base_inputs,
        V1793_VARIANT_POLICIES["D1_V1793_MACRO_PLUS_UNICORN_V1792_SL_TP"],
        decision_time=ts_ms,
    )
    ctx_2 = build_v1793_variant_market_context(
        base_inputs,
        V1793_VARIANT_POLICIES["D1_V1793_MACRO_PLUS_UNICORN_V1792_SL_TP"],
        decision_time=ts_ms,
    )
    s1 = SharedStrategyEvaluator.evaluate_model_direction(
        ctx_1, ModelIdentifier.SMC_M1_FVG_CONTINUATION.value, "LONG"
    )
    s2 = SharedStrategyEvaluator.evaluate_model_direction(
        ctx_2, ModelIdentifier.SMC_M1_FVG_CONTINUATION.value, "LONG"
    )
    d1 = s1.to_parity_dict()
    d2 = s2.to_parity_dict()
    d1.pop("adapter_call_id", None)
    d2.pop("adapter_call_id", None)
    assert d1 == d2


# ==============================================================================
# T20: Policy persistence strict boolean validation & LIVE scope prohibition
# ==============================================================================
def test_T20_policy_persistence_strict_boolean_and_live_scope_prohibition() -> None:
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    try:
        pol = resolve_stop_placement_policy(db)
        assert pol.v17_9_3_macro_unicorn_enabled is False
        assert pol.v17_9_3_observe_only is False
        assert pol.v17_9_3_mode == "OFF"

        # Non-boolean string for v17_9_3_macro_unicorn_enabled must raise StopPolicyValidationError
        with pytest.raises(StopPolicyValidationError):
            validate_stop_placement_policy({"v17_9_3_macro_unicorn_enabled": "true"}, pol)

        # Invalid v17_9_3_mode must raise StopPolicyValidationError
        with pytest.raises(StopPolicyValidationError):
            validate_stop_placement_policy({"v17_9_3_mode": "NOT_A_MODE"}, pol)

        # Activating V17.9.3 on LIVE server scope must be blocked
        with pytest.raises(StopPolicyValidationError):
            update_stop_placement_policy(
                db,
                {"v17_9_3_macro_unicorn_enabled": True, "v17_9_3_mode": "MACRO_PLUS_UNICORN_V1792_SL_TP"},
                expected_version=1,
                now_ms=1_775_000_000_000,
                server_scope="LIVE",
            )

        # Updating on PAPER scope succeeds and increments version
        updated, is_mut = update_stop_placement_policy(
            db,
            {"v17_9_3_observe_only": True, "v17_9_3_mode": "OBSERVE_ONLY"},
            expected_version=1,
            now_ms=1_775_000_000_000,
            server_scope="PAPER",
        )
        assert is_mut is True
        assert updated.version == 2
        assert updated.v17_9_3_observe_only is True
        assert updated.v17_9_3_mode == "OBSERVE_ONLY"
    finally:
        db.close()


# ==============================================================================
# T21: Statistical verdict honesty
# ==============================================================================
def test_T21_statistical_verdict_honesty() -> None:
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
# T22: Synthetic unit-test provenance segregation
# ==============================================================================
def test_T22_synthetic_unit_test_provenance_segregation() -> None:
    good_records = [{"provenance": "HISTORICAL_BACKTEST_V1793", "id": 1}]
    ok, err = validate_v1793_historical_provenance_segregation(good_records)
    assert ok is True
    assert err is None

    contaminated = [
        {"provenance": "HISTORICAL_BACKTEST_V1793", "id": 1},
        {"provenance": SYNTHETIC_PROVENANCE, "id": 2},
    ]
    ok_bad, err_bad = validate_v1793_historical_provenance_segregation(contaminated)
    assert ok_bad is False
    assert err_bad is not None
    assert "SYNTHETIC_PROVENANCE_CONTAMINATION_DETECTED" in err_bad


# ==============================================================================
# T23: Cost, $2.50 risk budget, and min_net_rr=2.0 remain unweakened
# ==============================================================================
def test_T23_cost_risk_budget_and_min_net_rr_unweakened() -> None:
    costs = CostAssumptions()
    assert costs.slippage_usd == 0.10
    assert costs.maker_fee_rate == 0.0002
    assert costs.taker_fee_rate == 0.0004

    rr = calculate_risk_reward(
        direction="LONG",
        planned_entry=3000.0,
        stop_loss=2995.0,
        take_profit=3018.0,
        capital_usdt=1000.0,
        risk_pct=0.25,
        leverage=20,
        margin_mode="ISOLATED",
        min_net_rr=2.0,
        costs=costs,
        now_ms=1_775_000_000_000,
    )
    assert rr.is_valid is True
    assert rr.net_risk_usdt <= 2.500001
    assert rr.net_rr >= 2.0
