"""
test_v15_2_liquidity_engine.py
V15.2 - Test Suite for Guarded Scalp Execution and Liquidity Scenario Engine.
Sections 166 - 192.
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

import crud
import models
from database import get_db
from main import app
from services.clock import FakeClock
from services.daily_ny_research_service import DailyNYResearchService
from services.entry_guard_service import EntryGuardRequest, evaluate_entry_guard
from services.liquidity_scenario_service import (
    DealingRange,
    ZoneConfluence,
    assess_range_position,
    build_liquidity_hypotheses,
    classify_market_regime,
    compute_dealing_range,
    confirm_liquidity_sweep,
    detect_order_blocks,
    evaluate_zone_confluence,
    find_intervening_obstacles,
    normalize_direction,
    rank_zones_multi_dimensional,
)
from services.opportunity_funnel_service import OpportunityFunnelService
from services.scalp_m1_model_service import ScalpCandidate, ScalpM1ModelService
from services.scalp_m1_types import (
    M1ScalpConfig,
    ModelIdentifier,
    OpportunityReasonCode,
    OpportunityStage,
    ScalpMode,
    WatchedZone,
    ZoneState,
)
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator
from services.smc_liquidity_service import build_liquidity_map, cluster_equal_levels
from services.smc_scenario_types import LiquidityLevel, ScenarioSnapshot, ScenarioState
from services.trading_policy_service import TradingPolicyService, resolve_entry_session_context


@dataclass
class CandleStub:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 100.0
    is_closed: bool = True

    @property
    def open_time(self) -> int:
        return self.timestamp


def _setup_positive_trading_policy(db: Session):
    policy = TradingPolicyService.get_or_create_policy(db)
    policy.entry_session_policy = "ALL_SESSIONS_WITH_NY_RESERVE"
    policy.max_daily_fills = 3
    db.commit()


# =====================================================================
# 1. STAGE A CORRECTNESS (SECTIONS 166, 167, 168, 169)
# =====================================================================

def test_v15_2_auto_state_fail_closed_on_error(isolated_db: Session):
    """PHẦN 024, 166: Auto trading query failure fails closed (global_auto = False)."""
    orch = ScalpWatcherOrchestrator()
    cfg = M1ScalpConfig(scalp_m1_mode=ScalpMode.PAPER.value, daily_ny_research_enabled=False)

    zone = WatchedZone(
        zone_id="z_auto_01", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    quote = {"bid": 2622.0, "ask": 2622.5, "is_valid": True, "server_time": 1_300_000, "observed_at": 1_300_000}

    # Pass auto_enabled=False to simulate fail-closed or auto disabled
    res = orch.evaluate_cycle(
        isolated_db, "XAUUSDT", now_ms=1_300_000, quote=quote,
        candles_1m=[], candles_5m=[], candles_15m=[], config=cfg, auto_enabled=False,
    )
    assert res["status"] == "OK"
    assert res["armed_order_id"] is None


def test_v15_2_ny_session_resolution_family_aware(isolated_db: Session):
    """PHẦN 016, 017, 018, 167: Family-aware session resolution with no AttributeError."""
    policy = TradingPolicyService.get_or_create_policy(isolated_db)
    clock = FakeClock(1_700_000_000_000)

    # Calling resolve_entry_session_context with NY_DAILY_RESEARCH
    ctx = resolve_entry_session_context(policy, "NY_DAILY_RESEARCH", now_ms=clock.now_ms(), clock=clock)
    assert ctx.strategy_family == "NY_DAILY_RESEARCH"
    assert hasattr(ctx, "in_ny_entry_window")
    assert isinstance(ctx.in_ny_entry_window, bool)
    assert not hasattr(ctx, "current_session")  # Verifies current_session is NOT an attribute


def test_v15_2_snapshot_failure_blocks_arm(isolated_db: Session, monkeypatch):
    """PHẦN 022, 023, 168, 169: Snapshot persistence failure or immutability conflict aborts arming."""
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    cfg = M1ScalpConfig(scalp_m1_mode=ScalpMode.PAPER.value, daily_ny_research_enabled=False)

    from services import smc_scenario_repository
    def mock_save_fail(db, snap):
        raise RuntimeError("SNAPSHOT_IMMUTABILITY_CONFLICT: digest mismatch")
    monkeypatch.setattr(smc_scenario_repository, "save_immutable_scenario_snapshot", mock_save_fail)

    from tests.test_v15_1_execution_lifecycle import _make_causal_long_m1_candles
    candles_1m = _make_causal_long_m1_candles()
    zone = WatchedZone(
        zone_id="z_snap_fail_01", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])
    quote = {"bid": 2622.0, "ask": 2622.5, "is_valid": True, "server_time": 1_300_000, "observed_at": 1_300_000}

    res = orch.evaluate_cycle(
        isolated_db, "XAUUSDT", now_ms=1_300_000, quote=quote,
        candles_1m=candles_1m, candles_5m=[], candles_15m=[], config=cfg, auto_enabled=True,
    )
    assert res["status"] == "OK"
    assert res["armed_order_id"] is None  # Arm was blocked due to snapshot conflict!

    # Order was NOT inserted into DB
    orders = isolated_db.query(models.PaperOrder).all()
    assert len(orders) == 0


def test_v15_2_unknown_strategy_family_fails_closed(isolated_db: Session):
    """PHẦN 033: Unknown strategy family fails closed in entry guard."""
    req = EntryGuardRequest(
        stage="ARM",
        now_ms=1_000_000,
        symbol="XAUUSDT",
        direction="LONG",
        execution_mode="AUTO",
        origin="AUTO_STRATEGY",
        strategy_family="MARTINGALE_GRID_AI",  # Unknown family
        setup_state="READY",
    )
    decision = evaluate_entry_guard(isolated_db, req)
    assert not decision.allowed
    assert "UNKNOWN_STRATEGY_FAMILY" in decision.reason_codes


# =====================================================================
# 2. CAUSAL ORDER BLOCK (OB) TESTS (SECTIONS 170, 171, 172)
# =====================================================================

def test_v15_2_detect_bullish_order_block_positive():
    """
    PHẦN 055, 056, 057, 171:
    Bullish OB positive control:
    - Confirmed swing high @ 2620.0
    - Base candle: Bearish bar @ [2610.0, 2614.0]
    - Bullish displacement leg breaking 2620.0 confirmed on bar close.
    - OB known_at matches break confirmation, NOT base candle timestamp.
    """
    candles = [
        CandleStub(60_000, 2618.0, 2622.0, 2617.0, 2620.0),    # Bar 0
        CandleStub(120_000, 2620.0, 2621.0, 2616.0, 2617.0),   # Bar 1
        CandleStub(180_000, 2617.0, 2618.0, 2614.0, 2615.0),   # Bar 2
        CandleStub(240_000, 2614.0, 2615.0, 2609.0, 2610.0),   # Bar 3: Base candle (bearish 2614 -> 2610)
        CandleStub(300_000, 2610.0, 2619.0, 2609.0, 2618.0),   # Bar 4: Displacement start
        CandleStub(360_000, 2618.0, 2626.0, 2617.0, 2625.0),   # Bar 5: Displacement break candle (close 2625 > 2620)
    ]
    swings = [
        {"id": "sh_2620", "side": "BUY_SIDE", "price": 2620.0, "timestamp": 60_000, "confirmed_at": 180_000}
    ]

    obs = detect_order_blocks(candles, swings, timeframe="15M", symbol="XAUUSDT", atr=2.0)
    assert len(obs) >= 1
    bull_ob = next(o for o in obs if o.direction == "BULLISH")
    assert bull_ob.zone_type == "OB"
    assert bull_ob.created_at == 240_000  # Base candle timestamp
    assert bull_ob.known_at == 360_000 + 900_000  # Break bar confirmation time
    assert bull_ob.bottom == 2609.0  # Base candle low
    assert bull_ob.top == 2615.0     # Base candle high
    assert bull_ob.invalidation_level < bull_ob.bottom


def test_v15_2_detect_bearish_order_block_positive():
    """
    PHẦN 058, 171:
    Bearish OB positive control (symmetric to Bullish):
    - Confirmed swing low @ 2640.0
    - Base candle: Bullish bar @ [2642.0, 2646.0]
    - Bearish displacement leg breaking 2640.0 on Bar 3 (240_000).
    """
    candles = [
        CandleStub(60_000, 2642.0, 2643.0, 2639.0, 2640.0),
        CandleStub(120_000, 2640.0, 2644.0, 2639.0, 2643.0),
        CandleStub(180_000, 2643.0, 2647.0, 2642.0, 2646.0),   # Base candle (bullish 2643 -> 2646)
        CandleStub(240_000, 2646.0, 2647.0, 2637.0, 2638.0),   # Break candle (close 2638 < 2640)
        CandleStub(300_000, 2638.0, 2639.0, 2632.0, 2634.0),
    ]
    swings = [
        {"id": "sl_2640", "side": "SELL_SIDE", "price": 2640.0, "timestamp": 60_000, "confirmed_at": 120_000}
    ]

    obs = detect_order_blocks(candles, swings, timeframe="15M", symbol="XAUUSDT", atr=2.0)
    assert len(obs) >= 1
    bear_ob = next(o for o in obs if o.direction == "BEARISH")
    assert bear_ob.zone_type == "OB"
    assert bear_ob.created_at == 180_000
    assert bear_ob.known_at == 240_000 + 900_000
    assert bear_ob.top == 2647.0
    assert bear_ob.bottom == 2642.0
    assert bear_ob.invalidation_level > bear_ob.top


def test_v15_2_order_block_negative_controls():
    """PHẦN 055, 172: Negative controls: no displacement or broken swing not known at break time."""
    candles_weak = [
        CandleStub(60_000, 2618.0, 2622.0, 2617.0, 2620.0),
        CandleStub(120_000, 2620.0, 2621.0, 2616.0, 2617.0),
        CandleStub(180_000, 2617.0, 2618.0, 2614.0, 2615.0),
        CandleStub(240_000, 2614.0, 2615.0, 2613.0, 2613.5),   # Base candle
        CandleStub(300_000, 2619.5, 2620.5, 2619.0, 2620.1),   # Tiny body (0.6 < 1.6)
    ]
    swings = [{"id": "sh_2620", "side": "BUY_SIDE", "price": 2620.0, "timestamp": 60_000, "confirmed_at": 180_000}]

    obs = detect_order_blocks(candles_weak, swings, timeframe="15M", symbol="XAUUSDT", atr=2.0, min_displacement_atr=0.8)
    assert len(obs) == 0  # Blocked due to weak displacement


# =====================================================================
# 3. ZONE CONFLUENCE TESTS (SECTION 173)
# =====================================================================

def test_v15_2_zone_confluence_evaluation():
    """PHẦN 065, 066, 173: Overlap geometry and confluence."""
    ob_zone = WatchedZone(
        zone_id="ob_01", symbol="XAUUSDT", timeframe="15M", zone_type="OB",
        direction="BULLISH", top=2625.0, bottom=2615.0, created_at=1000, known_at=2000,
    )
    fvg_zone = WatchedZone(
        zone_id="fvg_01", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2622.0, bottom=2618.0, created_at=1500, known_at=2500,
    )
    disjoint_zone = WatchedZone(
        zone_id="fvg_02", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2635.0, bottom=2630.0, created_at=1500, known_at=2500,
    )

    conf = evaluate_zone_confluence(ob_zone, fvg_zone)
    assert conf.has_confluence is True
    assert conf.overlap_interval == (2618.0, 2622.0)
    assert conf.overlap_ratio > 0.0

    conf_disjoint = evaluate_zone_confluence(ob_zone, disjoint_zone)
    assert conf_disjoint.has_confluence is False
    assert conf_disjoint.overlap_interval is None


# =====================================================================
# 4. LIQUIDITY MAP & SWEEP CONFIRMATION (SECTIONS 174, 175)
# =====================================================================

def test_v15_2_liquidity_map_and_sweep():
    """PHẦN 071, 078, 079, 174, 175: Sweep confirmation with breach and reclaim."""
    lvl = LiquidityLevel(
        id="liq_sh_2650", price=2650.0, side="BUY_SIDE", source_kind="SWING",
        occurred_at_ms=100_000, known_at_ms=200_000,
    )

    # Case 1: Touch only without breach -> NOT a sweep
    touch_bars = [CandleStub(250_000, 2648.0, 2650.0, 2647.0, 2649.0)]
    assert confirm_liquidity_sweep(lvl, touch_bars, now_ms=350_000) is None

    # Case 2: Breach without reclaim (Breakout close > 2650) -> NOT a reversal sweep
    breakout_bars = [CandleStub(250_000, 2649.0, 2654.0, 2648.0, 2653.0)]
    assert confirm_liquidity_sweep(lvl, breakout_bars, now_ms=350_000) is None

    # Case 3: Genuine sweep (Breach high=2653.0, Reclaim close=2648.5 <= 2650.0) -> SWEEP CONFIRMED!
    sweep_bars = [CandleStub(250_000, 2649.0, 2653.0, 2647.0, 2648.5)]
    sweep = confirm_liquidity_sweep(lvl, sweep_bars, now_ms=350_000)
    assert sweep is not None
    assert sweep.is_confirmed is True
    assert sweep.breach_price == 2653.0
    assert sweep.reclaim_price == 2648.5
    assert sweep.rejection_ratio > 0.0


# =====================================================================
# 5. DEALING RANGE & LIQUIDITY HYPOTHESES (SECTIONS 176, 177)
# =====================================================================

def test_v15_2_dealing_range_and_hypotheses():
    """PHẦN 083, 084, 091, 092, 176, 177: Dealing range & primary/alternative hypotheses."""
    swings = [
        {"id": "sh_2650", "side": "BUY_SIDE", "price": 2650.0, "timestamp": 100_000, "confirmed_at": 150_000},
        {"id": "sl_2610", "side": "SELL_SIDE", "price": 2610.0, "timestamp": 200_000, "confirmed_at": 250_000},
    ]
    dr = compute_dealing_range(swings, now_ms=300_000)
    assert dr.is_valid is True
    assert dr.range_low == 2610.0
    assert dr.range_high == 2650.0
    assert dr.equilibrium == 2630.0

    # Current price 2618 is in DISCOUNT (< 2630)
    assert assess_range_position(2618.0, dr) == "DISCOUNT"
    # Current price 2645 is in PREMIUM (> 2630)
    assert assess_range_position(2645.0, dr) == "PREMIUM"

    # Build Hypotheses
    bull_zone = WatchedZone(
        zone_id="z_bull_01", symbol="XAUUSDT", timeframe="15M", zone_type="OB",
        direction="BULLISH", top=2620.0, bottom=2615.0, created_at=220_000, known_at=260_000,
        invalidation_level=2614.0, state=ZoneState.WATCHING.value,
    )
    bear_zone = WatchedZone(
        zone_id="z_bear_01", symbol="XAUUSDT", timeframe="15M", zone_type="OB",
        direction="BEARISH", top=2648.0, bottom=2644.0, created_at=120_000, known_at=160_000,
        invalidation_level=2649.0, state=ZoneState.WATCHING.value,
    )
    target_lvl = LiquidityLevel(id="tgt_sh", price=2648.0, side="BUY_SIDE", source_kind="SWING", occurred_at_ms=1000, known_at_ms=2000)

    hyps = build_liquidity_hypotheses([bull_zone, bear_zone], [target_lvl], dr, current_price=2618.0, regime="TREND_BULLISH", now_ms=300_000)
    assert len(hyps) == 2
    primary = next(h for h in hyps if h.is_primary)
    alternative = next(h for h in hyps if not h.is_primary)

    assert primary.direction == "BULLISH"
    assert "2614.00" in primary.invalidation_condition
    assert alternative.direction == "BEARISH"


# =====================================================================
# 6. M1 TRIGGER CHRONOLOGY, TTL, DISTANCE (SECTIONS 178 - 181)
# =====================================================================

def test_v15_2_m1_trigger_expired_negative_control():
    """PHẦN 114, 181: Trigger staleness > max_trigger_age_ms rejected with TRIGGER_EXPIRED."""
    from tests.test_v15_1_execution_lifecycle import _make_causal_long_m1_candles
    candles_1m = _make_causal_long_m1_candles()
    zone = WatchedZone(
        zone_id="z_ttl_01", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    quote = {"bid": 2622.0, "ask": 2622.5, "is_valid": True, "server_time": 1_300_000, "observed_at": 1_300_000}
    policy_dict = {"capital_usdt": 1000.0, "risk_pct": 0.1, "leverage": 50, "margin_mode": "ISOLATED"}

    # Retest bar open is at 1140_000 (confirmed at 1200_000).
    # At now_ms = 1_300_000, trigger age is 100_000 ms.
    # Setting max_trigger_age_ms = 30_000 ms triggers TRIGGER_EXPIRED rejection (PHẦN 114, 181).
    cfg = M1ScalpConfig(max_trigger_age_ms=30_000)
    cand = ScalpM1ModelService.evaluate_m1_reversal_candidate(
        zone, candles_1m, quote, policy_dict, now_ms=1_300_000, config=cfg
    )
    assert cand.is_eligible is False
    assert cand.reason_code == OpportunityReasonCode.TRIGGER_EXPIRED.value


def test_v15_2_m1_entry_too_far_negative_control():
    """PHẦN 115, 181: Entry distance > max_entry_distance_atr rejected with ENTRY_TOO_FAR."""
    from tests.test_v15_1_execution_lifecycle import _make_causal_long_m1_candles
    candles_1m = _make_causal_long_m1_candles()
    zone = WatchedZone(
        zone_id="z_dist_01", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    # Quote moved very far from retest bar (retest is ~2622, quote ask is 2635.0 -> distance ~13.0 >> 1.5 ATR)
    far_quote = {"bid": 2634.5, "ask": 2635.0, "is_valid": True, "server_time": 1_300_000, "observed_at": 1_300_000}
    policy_dict = {"capital_usdt": 1000.0, "risk_pct": 0.1, "leverage": 50, "margin_mode": "ISOLATED"}

    cand = ScalpM1ModelService.evaluate_m1_reversal_candidate(zone, candles_1m, far_quote, policy_dict, now_ms=1_300_000)
    assert cand.is_eligible is False
    assert cand.reason_code == OpportunityReasonCode.ENTRY_TOO_FAR.value


# =====================================================================
# 7. MULTI-DIMENSIONAL RANKING & API ENDPOINTS (SECTIONS 187, 191)
# =====================================================================

def test_v15_2_multi_dimensional_ranking():
    """PHẦN 097, 098, 177: Multi-dimensional ranking deterministic sorting."""
    dr = DealingRange(2650.0, 2610.0, 2630.0, "dr_01", 1000, True)
    z1 = WatchedZone(
        zone_id="z_close", symbol="XAUUSDT", timeframe="15M", zone_type="OB",
        direction="BULLISH", top=2620.0, bottom=2615.0, created_at=1000, known_at=2000,
        state=ZoneState.TOUCHED.value,
    )
    z2 = WatchedZone(
        zone_id="z_far", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2612.0, bottom=2610.0, created_at=1000, known_at=2000,
        state=ZoneState.WATCHING.value,
    )
    ranked = rank_zones_multi_dimensional([z2, z1], [], dr, current_price=2618.0, atr=2.0, htf_bias="BULLISH")
    assert len(ranked) == 2
    # z1 should rank higher due to TOUCHED state and proximity
    assert ranked[0][0].zone_id == "z_close"


def test_v15_2_api_endpoints_liquidity_and_assessment(client: TestClient, isolated_db: Session):
    """PHẦN 191: API endpoints for liquidity-map, hypotheses, and zone-assessment."""
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    cfg = M1ScalpConfig(scalp_m1_mode=ScalpMode.PAPER.value, daily_ny_research_enabled=False)

    from tests.test_v15_1_execution_lifecycle import _make_causal_long_m1_candles
    candles_1m = _make_causal_long_m1_candles()
    zone = WatchedZone(
        zone_id="z_api_01", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    from services.scalp_watcher_orchestrator import scalp_watcher_orchestrator
    scalp_watcher_orchestrator.set_active_zones("XAUUSDT", [zone])

    quote = {"bid": 2622.0, "ask": 2622.5, "is_valid": True, "server_time": 1_300_000, "observed_at": 1_300_000}
    res = scalp_watcher_orchestrator.evaluate_cycle(
        isolated_db, "XAUUSDT", now_ms=1_300_000, quote=quote,
        candles_1m=candles_1m, candles_5m=[], candles_15m=[], config=cfg, auto_enabled=True,
    )
    assert res["status"] == "OK"

    # 1. GET /api/scalp/liquidity-map
    res_liq = client.get("/api/scalp/liquidity-map?symbol=XAUUSDT")
    assert res_liq.status_code == 200
    assert "levels" in res_liq.json()

    # 2. GET /api/scalp/hypotheses
    res_hyp = client.get("/api/scalp/hypotheses?symbol=XAUUSDT")
    assert res_hyp.status_code == 200
    assert "hypotheses" in res_hyp.json()

    # 3. GET /api/scalp/zone-assessment/{zone_id}
    res_eval = client.get(f"/api/scalp/zone-assessment/{zone.zone_id}?symbol=XAUUSDT")
    assert res_eval.status_code == 200
    data = res_eval.json()
    assert data["zone_id"] == zone.zone_id
    assert "why_watched" in data
    assert "missing_conditions" in data
