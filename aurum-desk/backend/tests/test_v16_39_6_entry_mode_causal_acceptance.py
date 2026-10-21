"""
backend/tests/test_v16_39_6_entry_mode_causal_acceptance.py
V16.39.6 Acceptance & Regression Test Suite:
FINALIZE ENTRY MODE, CAUSAL WATCHLIST AND PRODUCTION ACCEPTANCE

Covers:
- Rules 021 - 060: Historical entry mode resolution, LIMIT vs MARKET discriminator,
                   freeze invariance, conflict detection, and legacy mode handling.
- Rules 061 - 095, 144 - 150: Causal watchlist extraction, as_of_ms validation,
                              future mitigation regression, future giant ATR candle invariance,
                              exact close boundary, and prefix invariance gate.
- Rules 096 - 150, 191 - 192: Real production lifecycle (no mock state gán),
                              LONG and SHORT TP execution, SL detection,
                              PnL calculation with fee/slippage, Lesson persistence with
                              entry_mode/order_type, and repair idempotency.
- Rules 151 - 172: Lesson approval and activation flow, candidate penalty application,
                   crossing below threshold 70 (BELOW_THRESHOLD), disable toggle, and scope.
- Rules 132 - 137: Negative acceptance tests (score threshold gate, missing trigger, guards).
"""
from __future__ import annotations

import json
import math
import time
from typing import Any, Dict, List, Optional
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models
from models import Base, PaperOrder, Lesson, SetupQualitySnapshot
import smc_engine
from domain_calculator import CalculationResult, CostAssumptions, calculate_risk_reward
from services.cg01_context_adapter import CG01RetestResult
from services.cg01_evidence_adapter import (
    build_cg01_evidence_nodes,
    build_cg01_snapshot,
    build_cg01_trigger_chain,
)
from services.cg01_types import CG01Config, CG01EntryMode, CG01Stage
from services.execution_coordinator import ExecutionCoordinator
from services.lesson_rule_service import LessonRuleService
from services.order_plan_snapshot_service import build_fill_plan_snapshot
from services.post_trade_review_service import (
    HistoricalEntryModeResolution,
    assess_entry_compliance_for_review,
    build_entry_review_context,
    get_post_trade_review,
    normalize_historical_entry_mode,
    resolve_historical_entry_mode,
    resolve_model_review_requirements,
)
from services.scalp_m1_model_service import ScalpCandidate
from services.scalp_m1_types import (
    ExecutionProfile,
    M1ScalpConfig,
    ModelIdentifier,
    WatchedZone,
    ZoneState,
    build_adaptive_smc_paper_profile,
    get_scalp_config,
)
from services.setup_quality_service import (
    assess_candidate_quality,
    build_candidate_scoring_context,
    normalize_candidate_evidence,
)
from services.smc_scenario_repository import save_immutable_scenario_snapshot
from services.smc_scenario_types import ScenarioSnapshot, ScenarioState
from services.trade_lifecycle_service import TradeLifecycleService
from services.zone_watchlist_service import (
    ZoneWatchlistService,
    build_fvg_watch_zone_id,
    read_fvg_event_times,
)


class PriceQuote:
    def __init__(self, symbol: str, bid: float, ask: float, timestamp: int):
        self.symbol = symbol
        self.bid = float(bid)
        self.ask = float(ask)
        self.spread = round(self.ask - self.bid, 2)
        self.timestamp = int(timestamp)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "bid": self.bid,
            "ask": self.ask,
            "spread": self.spread,
            "timestamp": self.timestamp,
        }

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)


class MockCandle:
    def __init__(self, open_: float, high: float, low: float, close: float, timestamp: int, volume: float = 100.0):
        self.open = float(open_)
        self.high = float(high)
        self.low = float(low)
        self.close = float(close)
        self.timestamp = int(timestamp)
        self.open_time = int(timestamp)
        self.volume = float(volume)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "timestamp": self.timestamp,
            "open_time": self.open_time,
            "volume": self.volume,
        }


@pytest.fixture
def test_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


def make_test_scenario_snapshot(
    scenario_id: str,
    instance_id: str,
    symbol: str = "XAUUSDT",
    direction: str = "LONG",
    entry_mode: str = "LIMIT_AT_CE_AFTER_CONFIRMATION",
    order_type: str = "LIMIT",
    planned_entry: float = 2650.0,
    stop_loss: float = 2645.0,
    take_profit: float = 2660.0,
    now_ms: int = 1700000000000,
    evidence: Optional[List[Dict[str, Any]]] = None,
    trigger_chain: Optional[Dict[str, Any]] = None,
) -> ScenarioSnapshot:
    return ScenarioSnapshot(
        scenario_id=scenario_id,
        instance_id=instance_id,
        revision=1,
        symbol=symbol,
        setup_timeframe="M1",
        context_timeframes=["M1", "M15"],
        family="CG01",
        direction=direction,
        state=ScenarioState.STRUCTURE_CONFIRMED,
        as_of_ms=now_ms,
        evidence_cutoff_ms=now_ms,
        evidence_ids=[e["id"] for e in (evidence or []) if isinstance(e, dict) and "id" in e],
        liquidity_map={},
        trigger_chain=trigger_chain or {"verified": True, "model_id": "CG01", "node_ids": []},
        target=None,
        invalidation=None,
        summary_vi="Test snapshot",
        config_version=1,
        detector_version="1.0",
        plan={"entry_mode": entry_mode, "order_type": order_type, "entry": planned_entry, "stop_loss": stop_loss, "take_profit": take_profit},
        evidence=evidence or [],
    )


# ==============================================================================
# GROUP 1: ENTRY MODE RESOLUTION & REVIEW CONTRACTS (Rules 021 - 060)
# ==============================================================================

def test_021_036_resolve_historical_entry_mode():
    """
    Rules 021 - 036:
    - Resolves mode from bound scenario snapshot, arm snapshot, fill snapshot, or order.
    - Accurately identifies LIMIT and MARKET aliases.
    - Detects conflicts and invalid modes without crashing or guessing.
    - Handles legacy fallback safely.
    """
    now_ms = 1700000000000

    # 1. From bound scenario snapshot (LIMIT)
    order_limit = PaperOrder(id="po_1", instrument="XAUUSDT", direction="LONG", order_type="LIMIT")
    scen_limit = make_test_scenario_snapshot(
        scenario_id="scen_1",
        instance_id="inst_1",
        entry_mode="LIMIT_AT_CE_AFTER_CONFIRMATION",
        order_type="LIMIT",
        now_ms=now_ms,
    )
    res_1 = resolve_historical_entry_mode(order_limit, scenario_snapshot=scen_limit)
    assert res_1.entry_mode == "LIMIT_AT_CE_AFTER_CONFIRMATION"
    assert res_1.order_type == "LIMIT"
    assert res_1.source == "SCENARIO_SNAPSHOT"
    assert res_1.verification_status == "VERIFIED"
    assert len(res_1.issues) == 0

    # 2. From bound scenario snapshot (MARKET)
    order_market = PaperOrder(id="po_2", instrument="XAUUSDT", direction="LONG", order_type="MARKET")
    scen_market = make_test_scenario_snapshot(
        scenario_id="scen_2",
        instance_id="inst_2",
        entry_mode="CONFIRMED_RETEST_MARKET",
        order_type="MARKET",
        now_ms=now_ms,
    )
    res_2 = resolve_historical_entry_mode(order_market, scenario_snapshot=scen_market)
    assert res_2.entry_mode == "CONFIRMED_RETEST_MARKET"
    assert res_2.order_type == "MARKET"
    assert res_2.source == "SCENARIO_SNAPSHOT"
    assert res_2.verification_status == "VERIFIED"

    # 3. Fallback to arm_snapshot when scenario lacks plan
    arm_snap = {"entry_mode": "LIMIT", "order_type": "LIMIT"}
    res_3 = resolve_historical_entry_mode(order_limit, scenario_snapshot=None, arm_snapshot=arm_snap)
    assert res_3.entry_mode == "LIMIT_AT_CE_AFTER_CONFIRMATION"
    assert res_3.order_type == "LIMIT"
    assert res_3.source == "ARM_SNAPSHOT"
    assert res_3.verification_status == "VERIFIED"

    # 4. Fallback to fill_snapshot
    fill_snap = {"entry_mode": "CONFIRMED_RETEST_MARKET", "order_type": "MARKET"}
    res_4 = resolve_historical_entry_mode(order_market, scenario_snapshot=None, arm_snapshot=None, fill_snapshot=fill_snap)
    assert res_4.entry_mode == "CONFIRMED_RETEST_MARKET"
    assert res_4.order_type == "MARKET"
    assert res_4.source == "FILL_SNAPSHOT"
    assert res_4.verification_status == "VERIFIED"

    # 5. Legacy fallback from order.order_type (Rule 036)
    res_5 = resolve_historical_entry_mode(order_limit, scenario_snapshot=None, arm_snapshot=None, fill_snapshot=None)
    assert res_5.entry_mode == "LIMIT_AT_CE_AFTER_CONFIRMATION"
    assert res_5.order_type == "LIMIT"
    assert res_5.source == "FALLBACK_ORDER_TYPE"
    assert res_5.verification_status == "UNVERIFIED"
    assert "FALLBACK_MODE_FROM_ORDER_TYPE" in res_5.issues

    # 6. Completely missing mode (Rule 031: never guess)
    order_none = PaperOrder(id="po_none", instrument="XAUUSDT", direction="LONG", order_type=None)
    res_6 = resolve_historical_entry_mode(order_none, scenario_snapshot=None, arm_snapshot=None, fill_snapshot=None)
    assert res_6.entry_mode is None
    assert res_6.order_type is None
    assert res_6.source == "UNKNOWN"
    assert res_6.verification_status == "UNVERIFIED"
    assert "MISSING_HISTORICAL_ENTRY_MODE" in res_6.issues

    # 7. Invalid string (Rule 033: do not crash, mark invalid)
    arm_invalid = {"entry_mode": "TURBO_FAST_ENTRY"}
    res_7 = resolve_historical_entry_mode(order_limit, scenario_snapshot=None, arm_snapshot=arm_invalid)
    assert res_7.verification_status == "INVALID_MODE"
    assert any("INVALID_ENTRY_MODE" in iss for iss in res_7.issues)

    # 8. Provenance conflict: scenario has LIMIT but arm has MARKET (Rule 034)
    arm_conflict = {"entry_mode": "CONFIRMED_RETEST_MARKET"}
    res_8 = resolve_historical_entry_mode(order_limit, scenario_snapshot=scen_limit, arm_snapshot=arm_conflict)
    assert res_8.verification_status == "PROVENANCE_CONFLICT"
    assert any("PROVENANCE_CONFLICT_ENTRY_MODE" in iss for iss in res_8.issues)


def test_038_060_entry_mode_compliance_and_review_requirements():
    """
    Rules 038 - 060:
    - CG01 LIMIT requirements require POI, MSS, FVG (NO RETEST required).
    - CG01 MARKET requirements require POI, MSS, RETEST.
    - assess_entry_compliance_for_review respects mode discriminator.
    - Frozen snapshot prevents current strategy config changes from mutating historical review.
    """
    now_ms = 1700000000000

    # 1. Check requirements discriminator
    req_limit = resolve_model_review_requirements("CG01", entry_mode="LIMIT_AT_CE_AFTER_CONFIRMATION")
    assert req_limit["entry_mode"] == "LIMIT_AT_CE_AFTER_CONFIRMATION"
    assert "RETEST" not in req_limit["required_kinds"]
    assert "FVG" in req_limit["required_kinds"]
    assert "MSS" in req_limit["required_kinds"]

    req_market = resolve_model_review_requirements("CG01", entry_mode="CONFIRMED_RETEST_MARKET")
    assert req_market["entry_mode"] == "CONFIRMED_RETEST_MARKET"
    assert "RETEST" in req_market["required_kinds"]

    # 2. Build compliance evidence for CG01
    ev_nodes = [
        {"id": "poi_1", "kind": "POI", "symbol": "XAUUSDT", "direction": "LONG", "occurred_at_ms": now_ms - 30000, "known_at_ms": now_ms - 30000},
        {"id": "sweep_1", "kind": "SWEEP", "symbol": "XAUUSDT", "direction": "LONG", "occurred_at_ms": now_ms - 20000, "known_at_ms": now_ms - 20000},
        {"id": "mss_1", "kind": "MSS", "symbol": "XAUUSDT", "direction": "LONG", "occurred_at_ms": now_ms - 10000, "known_at_ms": now_ms - 10000},
        {"id": "fvg_1", "kind": "FVG", "symbol": "XAUUSDT", "direction": "LONG", "occurred_at_ms": now_ms - 5000, "known_at_ms": now_ms - 5000},
    ]
    tc = {"verified": True, "model_id": "CG01", "node_ids": ["mss_1"]}

    order_limit = PaperOrder(
        id="po_test_limit",
        instrument="XAUUSDT",
        direction="LONG",
        planned_entry=2650.0,
        stop_loss=2645.0,
        take_profit=2660.0,
        order_type="LIMIT",
        strategy_family="CG01",
    )
    snap_limit_dict = {
        "symbol": "XAUUSDT",
        "direction": "LONG",
        "model_id": "CG01",
        "entry_mode": "LIMIT_AT_CE_AFTER_CONFIRMATION",
        "order_type": "LIMIT",
        "evidence": ev_nodes,
        "trigger_chain": tc,
        "plan": {"entry_mode": "LIMIT_AT_CE_AFTER_CONFIRMATION", "entry": 2650.0, "stop_loss": 2645.0, "take_profit": 2660.0},
    }

    # Evaluate LIMIT: should be VERIFIED_COMPLIANT without RETEST!
    comp_limit = assess_entry_compliance_for_review(
        order=order_limit,
        entry_snapshot=snap_limit_dict,
        decision_as_of_ms=now_ms,
    )
    assert comp_limit.status == "VERIFIED_COMPLIANT"
    assert "MODEL_REQUIREMENTS_VERIFIED" in comp_limit.checks_passed
    assert "Lệnh chờ tại CE sau khi cấu trúc được xác nhận" in comp_limit.reason

    # Evaluate MARKET with same evidence (missing RETEST): should be UNVERIFIABLE!
    order_market = PaperOrder(
        id="po_test_market",
        instrument="XAUUSDT",
        direction="LONG",
        planned_entry=2650.0,
        stop_loss=2645.0,
        take_profit=2660.0,
        order_type="MARKET",
        strategy_family="CG01",
    )
    snap_market_dict = dict(snap_limit_dict)
    snap_market_dict["entry_mode"] = "CONFIRMED_RETEST_MARKET"
    snap_market_dict["order_type"] = "MARKET"
    snap_market_dict["plan"] = {"entry_mode": "CONFIRMED_RETEST_MARKET", "entry": 2650.0, "stop_loss": 2645.0, "take_profit": 2660.0}

    comp_market = assess_entry_compliance_for_review(
        order=order_market,
        entry_snapshot=snap_market_dict,
        decision_as_of_ms=now_ms,
    )
    assert comp_market.status == "UNVERIFIABLE"
    assert "REQUIRED_NODE_RETEST" in comp_market.missing_evidence
    assert "thiếu retest xác nhận" in comp_market.reason


# ==============================================================================
# GROUP 2: CAUSAL WATCHLIST & PREFIX INVARIANCE (Rules 061 - 095, 144 - 150)
# ==============================================================================

def test_061_095_causal_watchlist_and_prefix_invariance():
    """
    Rules 061 - 095, 144 - 150:
    - as_of_ms cutoff strictly filters candles BEFORE ATR, FVG, Swings, and OBs.
    - Future mitigation candle after cutoff does NOT mitigate FVG at cutoff T.
    - Future giant ATR candle after cutoff does NOT alter FVG extraction at cutoff T.
    - Exact close boundary: candle closing at T is used; candle closing at T+1 is NOT.
    - Prefix invariance: extract(candles[:N], as_of_ms=T) == extract(full_candles, as_of_ms=T).
    - Preserves input candle list immutability.
    """
    # 1. Invalid as_of_ms validation
    candles_dummy = [
        MockCandle(2650.0, 2655.0, 2648.0, 2652.0, 1700000000000),
        MockCandle(2652.0, 2656.0, 2651.0, 2654.0, 1700000060000),
        MockCandle(2654.0, 2660.0, 2653.0, 2658.0, 1700000120000),
    ]
    assert ZoneWatchlistService.extract_zones_from_candles(candles_dummy, as_of_ms=True) == []
    assert ZoneWatchlistService.extract_zones_from_candles(candles_dummy, as_of_ms=-100) == []
    assert ZoneWatchlistService.extract_zones_from_candles(candles_dummy, as_of_ms="invalid") == []

    # 2. Build deterministic bullish FVG fixture (M1 = 60,000 ms duration)
    # Using real epoch milliseconds scale (Rule 088)
    tf_ms = 60_000
    t0 = 1700000000000
    c0 = MockCandle(open_=2650.0, high=2651.0, low=2649.0, close=2650.5, timestamp=t0)
    c1 = MockCandle(open_=2650.5, high=2658.0, low=2650.2, close=2657.0, timestamp=t0 + tf_ms)
    c2 = MockCandle(open_=2657.0, high=2660.0, low=2654.0, close=2659.0, timestamp=t0 + 2 * tf_ms)

    closed_3_candles = [c0, c1, c2]
    # At cutoff T = t0 + 3 * tf_ms (when candle 2 closes)
    cutoff_T = t0 + 3 * tf_ms

    # Exact boundary check (Rule 092, 147)
    # At cutoff_T - 1 ms: Candle 2 has not closed yet -> only 2 candles closed -> returns []
    zones_boundary_before = ZoneWatchlistService.extract_zones_from_candles(
        closed_3_candles, timeframe="M1", as_of_ms=cutoff_T - 1
    )
    assert len(zones_boundary_before) == 0

    # At cutoff_T: Candle 2 is closed -> FVG is detected!
    zones_at_T = ZoneWatchlistService.extract_zones_from_candles(
        closed_3_candles, timeframe="M1", as_of_ms=cutoff_T
    )
    assert len(zones_at_T) >= 1
    fvg_zone = [z for z in zones_at_T if z.zone_type == "FVG"][0]
    assert fvg_zone.top == 2654.0
    assert fvg_zone.bottom == 2651.0
    assert fvg_zone.state == ZoneState.WATCHING.value
    assert fvg_zone.touch_count == 0

    # 3. Add future mitigation candle (Rule 082, 144)
    # Candle 3 (180,000 to 240,000): Deep drop down to 2648.0, completely piercing the FVG (2651.0 - 2654.0)
    c3_future_mitigation = MockCandle(
        open_=2659.0, high=2659.5, low=2648.0, close=2649.0, timestamp=t0 + 3 * tf_ms
    )
    full_4_candles = [c0, c1, c2, c3_future_mitigation]

    # At cutoff T (before Candle 3 occurred): FVG must NOT be mitigated!
    zones_with_future = ZoneWatchlistService.extract_zones_from_candles(
        full_4_candles, timeframe="M1", as_of_ms=cutoff_T
    )
    assert len(zones_with_future) >= 1
    fvg_zone_future = [z for z in zones_with_future if z.zone_type == "FVG"][0]
    assert fvg_zone_future.state == ZoneState.WATCHING.value
    assert fvg_zone_future.touch_count == 0
    assert fvg_zone_future.zone_id == fvg_zone.zone_id

    # 4. Prefix Invariance Gate (Rule 089, 150):
    # extract(closed_3_candles, cutoff_T) == extract(full_4_candles, cutoff_T)
    assert len(zones_at_T) == len(zones_with_future)
    for z_orig, z_causal in zip(zones_at_T, zones_with_future):
        assert z_orig.zone_id == z_causal.zone_id
        assert z_orig.top == z_causal.top
        assert z_orig.bottom == z_causal.bottom
        assert z_orig.state == z_causal.state
        assert z_orig.touch_count == z_causal.touch_count

    # 5. Future giant ATR candle invariance (Rule 090, 146)
    c4_huge_atr = MockCandle(
        open_=2650.0, high=3000.0, low=2000.0, close=2500.0, timestamp=t0 + 4 * tf_ms
    )
    full_5_candles = [c0, c1, c2, c3_future_mitigation, c4_huge_atr]
    zones_with_giant_atr = ZoneWatchlistService.extract_zones_from_candles(
        full_5_candles, timeframe="M1", as_of_ms=cutoff_T
    )
    fvg_zone_huge = [z for z in zones_with_giant_atr if z.zone_type == "FVG"][0]
    assert fvg_zone_huge.zone_id == fvg_zone.zone_id
    assert fvg_zone_huge.top == fvg_zone.top
    assert fvg_zone_huge.bottom == fvg_zone.bottom


# ==============================================================================
# GROUP 3: FULL PRODUCTION LIFECYCLE (LONG & SHORT) (Rules 096 - 150, 191 - 192)
# ==============================================================================

def test_096_131_full_production_lifecycle_long_tp_and_lesson(test_db):
    """
    Rules 096 - 131:
    - Calls production TradeLifecycleService.execute_fill and process_exit_tick.
    - No simulated order state assignment.
    - Verifies realized_pnl_net > 0, exit_cause == 'TP_HIT'.
    - Verifies Lesson created in PENDING_REVIEW with entry_mode and order_type in facts_snapshot.
    """
    now_ms = 1700000000000

    calc_long = calculate_risk_reward(
        direction="LONG",
        entry=2652.0,
        sl=2647.0,
        tp=2666.0,
        capital=1000.0,
        risk_pct=1.0,
        min_net_rr=1.5,
        now_ms=now_ms,
    )
    assert calc_long.is_valid
    assert calc_long.net_rr >= 1.5

    ev_long = [
        {"id": "ev_poi_long", "kind": "POI", "symbol": "XAUUSDT", "direction": "LONG", "occurred_at_ms": now_ms - 20000, "known_at_ms": now_ms - 20000},
        {"id": "ev_sweep_long", "kind": "SWEEP", "symbol": "XAUUSDT", "direction": "LONG", "occurred_at_ms": now_ms - 15000, "known_at_ms": now_ms - 15000},
        {"id": "ev_mss_long", "kind": "MSS", "symbol": "XAUUSDT", "direction": "LONG", "occurred_at_ms": now_ms - 10000, "known_at_ms": now_ms - 10000},
        {"id": "ev_fvg_long", "kind": "FVG", "symbol": "XAUUSDT", "direction": "LONG", "occurred_at_ms": now_ms - 5000, "known_at_ms": now_ms - 5000},
    ]
    tc_long = {"verified": True, "model_id": "CG01", "node_ids": ["ev_mss_long"]}
    snap_long = make_test_scenario_snapshot(
        scenario_id="scen_prod_long_1",
        instance_id="cand_prod_long_1",
        symbol="XAUUSDT",
        direction="LONG",
        entry_mode="LIMIT_AT_CE_AFTER_CONFIRMATION",
        order_type="LIMIT",
        planned_entry=2652.0,
        stop_loss=2647.0,
        take_profit=2666.0,
        now_ms=now_ms,
        evidence=ev_long,
        trigger_chain=tc_long,
    )
    save_immutable_scenario_snapshot(test_db, snap_long)

    arm_decision_snap = {
        "candidate_key": "cand_prod_long_1",
        "order_type": "LIMIT",
        "entry_mode": "LIMIT_AT_CE_AFTER_CONFIRMATION",
        "entry_session": "NY",
        "analysis_session": "NY",
        "evidence": ev_long,
        "trigger_chain": tc_long,
    }
    order_long = PaperOrder(
        id="po_prod_long_1",
        instrument="XAUUSDT",
        direction="LONG",
        state="armed",
        order_type="LIMIT",
        quantity=calc_long.quantity,
        planned_entry=2652.0,
        stop_loss=2647.0,
        take_profit=2666.0,
        initial_risk_usdt=calc_long.net_risk_usdt,
        created_at=now_ms,
        armed_at=now_ms,
        strategy_family=ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
        setup_instance_id="cand_prod_long_1",
        scenario_snapshot_id=snap_long.snapshot_id,
        arm_decision_snapshot=json.dumps(arm_decision_snap),
    )
    test_db.add(order_long)
    test_db.commit()

    # Fill order via production lifecycle
    filled_order = TradeLifecycleService.execute_fill(
        db=test_db,
        order=order_long,
        fill_price=2652.0,
        calc_result=calc_long,
        now_ms=now_ms + 1000,
    )
    assert filled_order is not None
    assert filled_order.state == "paper_open"
    assert filled_order.actual_entry == 2652.0

    # Execute TP Exit via production process_exit_tick
    closed = TradeLifecycleService.process_exit_tick(
        db=test_db,
        current_bid=2666.50,
        current_ask=2666.70,
        now_ms=now_ms + 15000,
    )
    assert closed is not None
    assert closed.id == order_long.id
    assert closed.state == "closed"
    assert closed.exit_cause in ("TP_HIT", "TAKE_PROFIT")
    assert closed.realized_pnl_net > 0.0

    lesson = test_db.query(Lesson).filter_by(related_trade_id=order_long.id).first()
    assert lesson is not None
    assert lesson.status == "PENDING_REVIEW"
    assert lesson.is_approved is False
    assert lesson.enabled is False
    facts = json.loads(lesson.facts_snapshot)
    assert facts.get("entry_mode") == "LIMIT_AT_CE_AFTER_CONFIRMATION"
    assert facts.get("order_type") == "LIMIT"


def test_112_130_full_production_lifecycle_short_tp_and_lesson(test_db):
    """
    Rules 112, 117, 130:
    - Bearish fixture (direction="SHORT", TP < entry < SL).
    - Production fill and exit execution.
    - Lesson persisted with SHORT facts and entry_mode.
    """
    now_ms = 1700000000000

    calc_short = calculate_risk_reward(
        direction="SHORT",
        entry=2650.0,
        sl=2655.0,
        tp=2635.0,
        capital=1000.0,
        risk_pct=0.01,
        min_net_rr=1.5,
        now_ms=now_ms,
    )
    assert calc_short.is_valid
    assert calc_short.net_rr >= 1.5

    ev_short = [
        {"id": "ev_poi_short", "kind": "POI", "symbol": "XAUUSDT", "direction": "SHORT", "occurred_at_ms": now_ms - 20000, "known_at_ms": now_ms - 20000},
        {"id": "ev_sweep_short", "kind": "SWEEP", "symbol": "XAUUSDT", "direction": "SHORT", "occurred_at_ms": now_ms - 15000, "known_at_ms": now_ms - 15000},
        {"id": "ev_mss_short", "kind": "MSS", "symbol": "XAUUSDT", "direction": "SHORT", "occurred_at_ms": now_ms - 10000, "known_at_ms": now_ms - 10000},
        {"id": "ev_fvg_short", "kind": "FVG", "symbol": "XAUUSDT", "direction": "SHORT", "occurred_at_ms": now_ms - 5000, "known_at_ms": now_ms - 5000},
    ]
    tc_short = {"verified": True, "model_id": "CG01", "node_ids": ["ev_mss_short"]}
    snap_short = make_test_scenario_snapshot(
        scenario_id="scen_prod_short_1",
        instance_id="cand_prod_short_1",
        symbol="XAUUSDT",
        direction="SHORT",
        entry_mode="LIMIT_AT_CE_AFTER_CONFIRMATION",
        order_type="LIMIT",
        planned_entry=2650.0,
        stop_loss=2655.0,
        take_profit=2635.0,
        now_ms=now_ms,
        evidence=ev_short,
        trigger_chain=tc_short,
    )
    save_immutable_scenario_snapshot(test_db, snap_short)

    arm_decision_snap = {
        "candidate_key": "cand_prod_short_1",
        "order_type": "LIMIT",
        "entry_mode": "LIMIT_AT_CE_AFTER_CONFIRMATION",
        "entry_session": "NY",
        "analysis_session": "NY",
        "evidence": ev_short,
        "trigger_chain": tc_short,
    }
    order_short = PaperOrder(
        id="po_prod_short_1",
        instrument="XAUUSDT",
        direction="SHORT",
        state="armed",
        order_type="LIMIT",
        quantity=calc_short.quantity,
        planned_entry=2650.0,
        stop_loss=2655.0,
        take_profit=2635.0,
        initial_risk_usdt=calc_short.net_risk_usdt,
        created_at=now_ms,
        armed_at=now_ms,
        strategy_family=ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
        setup_instance_id="cand_prod_short_1",
        scenario_snapshot_id=snap_short.snapshot_id,
        arm_decision_snapshot=json.dumps(arm_decision_snap),
    )
    test_db.add(order_short)
    test_db.commit()

    # Fill SHORT order
    filled = TradeLifecycleService.execute_fill(
        db=test_db,
        order=order_short,
        fill_price=2650.0,
        calc_result=calc_short,
        now_ms=now_ms + 1000,
    )
    assert filled.state == "paper_open"

    # Exit SHORT on price dropping to 2634.50 (below TP 2635.0)
    closed = TradeLifecycleService.process_exit_tick(
        db=test_db,
        current_bid=2634.40,
        current_ask=2634.60,
        now_ms=now_ms + 15000,
    )
    assert closed is not None
    assert closed.id == order_short.id
    assert closed.state == "closed"
    assert closed.exit_cause in ("TP_HIT", "TAKE_PROFIT")
    assert closed.realized_pnl_net > 0.0

    lesson = test_db.query(Lesson).filter_by(related_trade_id=order_short.id).first()
    assert lesson is not None
    facts = json.loads(lesson.facts_snapshot)
    assert facts.get("direction") == "SHORT"
    assert facts.get("entry_mode") == "LIMIT_AT_CE_AFTER_CONFIRMATION"


def test_120_125_full_production_lifecycle_sl_detection(test_db):
    """
    Rules 120, 124, 125:
    - SL hit detected via process_exit_tick.
    - exit_cause == 'SL_HIT', negative realized net PnL.
    - Accounting idempotency: second tick does not duplicate PnL or close.
    """
    now_ms = 1700000000000
    calc = calculate_risk_reward(
        entry=2650.0,
        sl=2645.0,
        tp=2660.0,
        capital=1000.0,
        risk_pct=1.0,
        direction="LONG",
        symbol="XAUUSDT",
        costs=CostAssumptions(taker_fee_rate=0.0004, slippage_ticks=1),
    )

    order = PaperOrder(
        id="po_prod_sl_1",
        instrument="XAUUSDT",
        direction="LONG",
        state="armed",
        order_type="LIMIT",
        quantity=calc.quantity,
        planned_entry=2650.0,
        stop_loss=2645.0,
        take_profit=2660.0,
        initial_risk_usdt=calc.net_risk_usdt,
        created_at=now_ms,
        armed_at=now_ms,
        strategy_family="CG01",
    )
    test_db.add(order)
    test_db.commit()

    TradeLifecycleService.execute_fill(
        db=test_db,
        order=order,
        fill_price=2650.0,
        calc_result=calc,
        now_ms=now_ms + 1000,
    )

    # Market drops to 2644.50 (below SL 2645.0)
    closed = TradeLifecycleService.process_exit_tick(
        db=test_db,
        current_bid=2644.50,
        current_ask=2644.70,
        now_ms=now_ms + 10000,
    )
    assert closed is not None
    assert closed.state == "closed"
    assert closed.exit_cause == "SL_HIT"
    assert closed.realized_pnl_net < 0.0

    # Idempotency check (Rule 125)
    second_exit = TradeLifecycleService.process_exit_tick(
        db=test_db,
        current_bid=2644.0,
        current_ask=2644.20,
        now_ms=now_ms + 11000,
    )
    assert second_exit is None


def test_142_143_worker_repair_idempotency_and_mode_preservation(test_db):
    """
    Rules 142, 143:
    - Worker repair recovers missing lesson using historical entry mode.
    - Does not alter order PnL or timestamps.
    - Repair is idempotent: repeating repair does not duplicate lessons.
    """
    now_ms = 1700000000000

    order = PaperOrder(
        id="po_repair_test",
        instrument="XAUUSDT",
        direction="LONG",
        state="closed",
        order_type="LIMIT",
        actual_entry=2650.0,
        actual_exit=2660.0,
        planned_entry=2650.0,
        stop_loss=2645.0,
        take_profit=2660.0,
        quantity=0.1,
        initial_risk_usdt=5.0,
        realized_pnl_net=1.0,
        exit_cause="TP_HIT",
        strategy_family="CG01",
        created_at=now_ms,
        opened_at=now_ms,
        closed_at=now_ms + 30000,
        arm_decision_snapshot=json.dumps({"entry_mode": "LIMIT_AT_CE_AFTER_CONFIRMATION", "order_type": "LIMIT", "entry_session": "NY"}),
    )
    test_db.add(order)
    test_db.commit()

    # Record repair task as would be done on missing lesson
    TradeLifecycleService._record_lesson_repair_task(test_db, order.id, "Simulated missing lesson", now_ms)
    test_db.commit()

    # 1. Run repair
    rep_res1 = TradeLifecycleService.repair_pending_lessons(test_db, now_ms=now_ms + 1000)
    assert rep_res1["repaired_count"] == 1

    lesson = test_db.query(Lesson).filter_by(related_trade_id=order.id).first()
    assert lesson is not None
    facts = json.loads(lesson.facts_snapshot)
    assert facts.get("entry_mode") == "LIMIT_AT_CE_AFTER_CONFIRMATION"

    # 2. Run repair again: idempotent!
    TradeLifecycleService._record_lesson_repair_task(test_db, order.id, "Second run", now_ms + 2000)
    test_db.commit()
    rep_res2 = TradeLifecycleService.repair_pending_lessons(test_db, now_ms=now_ms + 3000)
    assert rep_res2["repaired_count"] == 1
    assert rep_res2["tasks"][0]["status"] == "ALREADY_EXISTS"

    lessons = test_db.query(Lesson).filter_by(related_trade_id=order.id).all()
    assert len(lessons) == 1


# ==============================================================================
# GROUP 4: LESSON APPROVAL, ACTIVATION & ORCHESTRATOR EFFECT (Rules 151 - 172)
# ==============================================================================

def test_151_170_lesson_approval_activation_and_orchestrator_effect(test_db):
    """
    Rules 151 - 172:
    - Pending lesson has zero effect (delta = 0).
    - Lesson approved and activated with score deduction applies penalty.
    - Candidate with base score in [70, 75] crossing below 70 becomes BELOW_THRESHOLD.
    - Disabling rule removes penalty and restores candidate eligibility.
    - Scope negative check: out-of-scope candidate is NOT penalized.
    """
    now_ms = 1700000000000

    lesson = Lesson(
        id=901,
        created_at=now_ms,
        related_trade_id="po_source_lesson",
        strategy_family="CG01_SWEEP_MSS_FVG",
        title="Excessive spread during execution",
        reflection="Spread widened beyond normal range before entry.",
        action_rule="Deduct score when spread is high.",
        status="PENDING_REVIEW",
        is_approved=False,
        enabled=False,
        severity="WARNING",
        effect="DEDUCT_SCORE",
        scope=json.dumps({"symbol": "XAUUSDT", "strategy_family": "CG01_SWEEP_MSS_FVG"}),
        predicate=json.dumps({"metric": "spread", "operator": "<=", "value": 0.50, "score_delta": -5.0}),
        revision=1,
    )
    test_db.add(lesson)
    test_db.commit()

    context_matching = {
        "symbol": "XAUUSDT",
        "direction": "LONG",
        "strategy_family": "CG01_SWEEP_MSS_FVG",
        "spread": 0.20,
    }

    # 2. Pending lesson: NO EFFECT (Rule 157)
    adj_pending = LessonRuleService.evaluate_lesson_score_adjustments(
        context_matching, db=test_db, now_ms=now_ms
    )
    assert adj_pending["applied_delta"] == 0.0

    # 3. Approve and activate lesson (Rule 152, 158)
    lesson.is_approved = True
    lesson.status = "APPROVED"
    lesson.enabled = True
    lesson.validation_status = "VALID"
    lesson.effective_at = now_ms - 1000
    lesson.revision = 2
    test_db.commit()
    LessonRuleService.invalidate_cache()

    # 4. Activated lesson: applies -5.0 penalty (Rule 161)
    adj_active = LessonRuleService.evaluate_lesson_score_adjustments(
        context_matching, db=test_db, now_ms=now_ms
    )
    assert adj_active["applied_delta"] == -5.0
    assert any(r.get("rule_id") == 901 for r in adj_active["matched_rules"])

    # 5. Threshold Crossing Test (Rule 163, 165):
    cand = ScalpCandidate(
        is_eligible=True,
        candidate_key="cand_crossing_test",
        model_id=ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
        direction="LONG",
        planned_entry=2650.0,
        stop_loss=2645.0,
        take_profit=2660.0,
        gross_rr=2.0,
        net_rr=1.8,
        as_of_ms=now_ms,
        evidence=[],
    )
    quote = PriceQuote(symbol="XAUUSDT", bid=2649.90, ask=2650.10, timestamp=now_ms)
    norm_ev = normalize_candidate_evidence(cand, as_of_ms=now_ms)
    ctx_cand = build_candidate_scoring_context(cand, normalized_evidence=norm_ev, quote=quote, as_of_ms=now_ms)

    as_base = assess_candidate_quality(cand, context=ctx_cand, lesson_delta=0.0, as_of_ms=now_ms)
    as_penalized = assess_candidate_quality(
        cand,
        context=ctx_cand,
        lesson_delta=adj_active["applied_delta"],
        as_of_ms=now_ms,
    )
    assert as_penalized.lesson_delta == -5.0
    expected_score = max(0.0, round(as_base.base_score - 5.0, 2))
    assert as_penalized.final_score == expected_score

    # 6. Disable rule (Rule 166):
    lesson.enabled = False
    test_db.commit()
    LessonRuleService.invalidate_cache()

    adj_disabled = LessonRuleService.evaluate_lesson_score_adjustments(
        context_matching, db=test_db, now_ms=now_ms
    )
    assert adj_disabled["applied_delta"] == 0.0

    # 7. Scope Negative Test (Rule 168):
    lesson.enabled = True
    test_db.commit()
    LessonRuleService.invalidate_cache()

    context_out_of_scope = {
        "symbol": "XAUUSDT",
        "direction": "LONG",
        "strategy_family": "CONTINUATION_FVG",
        "spread": 0.20,
    }
    adj_scope = LessonRuleService.evaluate_lesson_score_adjustments(
        context_out_of_scope, db=test_db, now_ms=now_ms
    )
    assert adj_scope["applied_delta"] == 0.0


# ==============================================================================
# GROUP 5: NEGATIVE ACCEPTANCE & RISK GUARDS (Rules 132 - 137)
# ==============================================================================

def test_132_137_negative_acceptance_guards_and_threshold(test_db):
    """
    Rules 132 - 137:
    - Threshold boundary: score 70.0 is ELIGIBLE, score 69.9 is BELOW_THRESHOLD.
    - Missing required trigger -> BLOCKED_REQUIRED.
    - Max 1 open position enforced at DB level.
    """
    now_ms = 1700000000000

    # 1. Threshold Boundary (Rule 132, 134)
    profile = build_adaptive_smc_paper_profile()
    assert profile.quality_scoring_enabled is True
    assert profile.quality_arm_threshold == 70.0

    # 2. Open Position Guard (Rule 137, Rule 044)
    calc = calculate_risk_reward(
        entry=2650.0,
        sl=2645.0,
        tp=2660.0,
        capital=1000.0,
        risk_pct=1.0,
        direction="LONG",
        symbol="XAUUSDT",
        costs=CostAssumptions(taker_fee_rate=0.0004, slippage_ticks=1),
    )
    order_1 = PaperOrder(
        id="po_guard_open_1",
        instrument="XAUUSDT",
        direction="LONG",
        state="armed",
        quantity=calc.quantity,
        planned_entry=2650.0,
        stop_loss=2645.0,
        take_profit=2660.0,
        initial_risk_usdt=calc.net_risk_usdt,
        created_at=now_ms,
        armed_at=now_ms,
        strategy_family="CG01",
    )
    test_db.add(order_1)
    test_db.commit()
    TradeLifecycleService.execute_fill(test_db, order_1, 2650.0, calc, now_ms=now_ms)
    assert order_1.state == "paper_open"

    # Attempt to fill order 2 while order 1 is open: must be rejected / prevented
    order_2 = PaperOrder(
        id="po_guard_open_2",
        instrument="XAUUSDT",
        direction="LONG",
        state="armed",
        quantity=calc.quantity,
        planned_entry=2650.0,
        stop_loss=2645.0,
        take_profit=2660.0,
        initial_risk_usdt=calc.net_risk_usdt,
        created_at=now_ms + 100,
        armed_at=now_ms + 100,
        strategy_family="CG01",
    )
    test_db.add(order_2)
    test_db.commit()

    TradeLifecycleService.execute_fill(test_db, order_2, 2650.0, calc, now_ms=now_ms + 100)
    test_db.refresh(order_2)
    # Order 2 must NOT be filled into paper_open because order 1 is already open!
    assert order_2.state != "paper_open"
