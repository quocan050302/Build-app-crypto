"""
backend/tests/test_v16_opportunity_engine.py
V16 Comprehensive Test Suite: SMC Liquidity Opportunity Engine (Rules 041 - 192).

Covers:
1. Canonical timestamp accessor (candle_open_ms) with object/dict parity (Rules 041-045).
2. CHOCH & BOS evidence non-zero timestamp integrity (Rules 041-048, 156-158).
3. Multi-zone execution hypotheses authority (Rules 070-076, 161-163).
4. LiquidityCluster grouping, state transitions & secondary round numbers (Rules 051-069, 159-160).
5. SMC M1 FVG Continuation positive & negative controls (Rules 083-085, 164-165).
6. SMC M1 Sweep Reclaim positive & negative controls (Rules 086-089, 166-167).
7. Fail-closed SMC Model Registry (Rules 090-092, 168).
8. Candidate Router, Deduplication & Conflict Resolution (Rules 093-096, 169-170).
9. Reason-aware arm-rejection suppression (Rule 109).
10. Bitget Public Depth validation & quality assessment (Rules 113-125).
11. Daily Frequency Goal evaluation: 0->1->2->3 fills & NY slot reserve (Rules 126-140, 173-176).
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import crud
import models
from models import Base
from services.clock import FakeClock
from services.bitget_depth_service import validate_orderbook_levels
from services.liquidity_scenario_service import (
    build_execution_hypotheses_for_zones,
    check_candidate_lifecycle_authority,
    LifecycleCycleAuthority,
    HypothesisWriteOutcome,
    LifecycleStageResult,
)
from services.scalp_m1_model_service import (
    ScalpCandidate,
    ScalpM1ModelService,
    candle_open_ms,
    evaluate_smc_opportunity_candidates,
)
from services.scalp_m1_types import (
    M1ScalpConfig,
    ModelIdentifier,
    OpportunityReasonCode,
    WatchedZone,
    ZoneState,
)
from services.smc_liquidity_service import (
    LiquidityLevel,
    build_liquidity_clusters,
    build_liquidity_map,
    update_cluster_state,
)
from services.smc_model_registry import (
    SMC_MODEL_REGISTRY,
    get_model_spec,
    get_session_family,
    requires_zone_lifecycle_authority,
)
from services.smc_scenario_types import LiquidityCluster
from services.trading_policy_service import TradingPolicyService


@dataclass
class CandleBarStub:
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 100.0
    is_closed: bool = True

    @property
    def timestamp(self) -> int:
        return self.open_time


@pytest.fixture
def isolated_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


# ==============================================================================
# 1. TIMESTAMP ACCESSOR (Rules 041 - 045, 156)
# ==============================================================================

def test_candle_open_ms_accessor():
    """Rule 041-043, 156: Object/dict parity, positive ms contract, no epoch-0 fallback."""
    # Object with open_time
    class BarWithOpenTime:
        open_time = 1700000000000

    assert candle_open_ms(BarWithOpenTime()) == 1700000000000

    # Object with timestamp only (regression from audit)
    class BarWithTimestamp:
        timestamp = 1700000060000

    assert candle_open_ms(BarWithTimestamp()) == 1700000060000

    # Dict with open_time / timestamp
    assert candle_open_ms({"open_time": 1700000120000}) == 1700000120000
    assert candle_open_ms({"timestamp": "1700000180000"}) == 1700000180000
    assert candle_open_ms({"time": 1700000240000}) == 1700000240000

    # Missing / non-positive / invalid -> Returns None (never 0)
    assert candle_open_ms(None) is None
    assert candle_open_ms({}) is None
    assert candle_open_ms({"open_time": 0}) is None
    assert candle_open_ms({"open_time": -100}) is None
    assert candle_open_ms({"open_time": "invalid_string"}) is None


# ==============================================================================
# 2. CHOCH & BOS TIMESTAMP INTEGRITY (Rules 041 - 048, 157)
# ==============================================================================

def test_choch_and_bos_evidence_timestamps():
    """Rule 041-048, 157: CHOCH and BOS evidence have non-zero timestamps and stable IDs."""
    cfg = M1ScalpConfig(min_closed_m1_bars=15)

    # Synthetic M1 candles with timestamps
    candles = [
        CandleBarStub(60_000 * i, 2600.0, 2605.0, 2598.0, 2602.0)
        for i in range(1, 20)
    ]
    # Continuation candidate with clear trend
    quote = {"bid": 2602.0, "ask": 2602.5, "is_valid": True}
    res = ScalpM1ModelService.evaluate_m1_continuation_candidate(
        candles, candles, quote, "BULLISH", "BULLISH", {"capital_usdt": 1000.0, "risk_pct": 0.25, "leverage": 50},
        now_ms=1300_000, config=cfg
    )
    if res.is_eligible:
        for ev in res.evidence:
            assert ev["occurred_at_ms"] > 0
            assert ev["known_at_ms"] >= ev["occurred_at_ms"]
            assert not ev["id"].endswith("_0")


# ==============================================================================
# 3. MULTI-ZONE EXECUTION HYPOTHESES (Rules 070 - 080, 161 - 163)
# ==============================================================================

def test_multi_zone_execution_hypotheses_authority():
    """Rule 070-076, 161: build_execution_hypotheses_for_zones creates hypotheses for ALL bounded zones."""
    zones = [
        WatchedZone(
            zone_id=f"z_{i}", symbol="XAUUSDT", timeframe="15M", zone_type="OB",
            direction="BULLISH" if i % 2 == 0 else "BEARISH",
            top=2620.0 + i * 5.0, bottom=2615.0 + i * 5.0, created_at=60_000, known_at=60_000,
            state=ZoneState.CONFIRMED.value
        )
        for i in range(5)
    ]
    now_ms = 1_000_000
    hyps = build_execution_hypotheses_for_zones(zones, current_price=2630.0, as_of_ms=now_ms)

    assert len(hyps) == 5
    assert {h.watched_zone.zone_id for h in hyps} == {"z_0", "z_1", "z_2", "z_3", "z_4"}

    # Monotonic identity: same zone facts produce same hypothesis_id
    hyps_second_run = build_execution_hypotheses_for_zones(zones, current_price=2632.0, as_of_ms=now_ms + 10_000)
    for h1, h2 in zip(hyps, hyps_second_run):
        assert h1.hypothesis_id == h2.hypothesis_id


# ==============================================================================
# 4. LIQUIDITY CLUSTERS & ROUND NUMBERS (Rules 051 - 069, 159 - 160)
# ==============================================================================

def test_liquidity_clusters_grouping_and_state_transitions():
    """Rule 051-063, 159-160: Cluster causal grouping, dedup, and state transitions."""
    now_ms = 1_000_000
    levels = [
        LiquidityLevel(
            id="sh_1", price=2650.0, side="BUY_SIDE", source_kind="SWING",
            occurred_at_ms=100_000, known_at_ms=120_000, state="UNTOUCHED"
        ),
        LiquidityLevel(
            id="sh_2", price=2650.4, side="BUY_SIDE", source_kind="SWING",
            occurred_at_ms=110_000, known_at_ms=130_000, state="UNTOUCHED"
        ),
        LiquidityLevel(
            id="sl_1", price=2600.0, side="SELL_SIDE", source_kind="SWING",
            occurred_at_ms=90_000, known_at_ms=110_000, state="UNTOUCHED"
        ),
    ]

    clusters = build_liquidity_clusters(levels, timeframe="15M", symbol="XAUUSDT", now_ms=now_ms, tolerance=0.50)
    assert len(clusters) == 2  # Buy-side cluster at ~2650.2, Sell-side at 2600.0

    buy_cluster = next(c for c in clusters if c.side == "BUY_SIDE")
    assert len(buy_cluster.source_level_ids) == 2
    assert buy_cluster.state == "UNSWEPT"
    assert buy_cluster.price_band == (2650.0, 2650.4)

    # State transition: candle pierces above cluster top and closes below -> RECLAIMED / SWEPT
    sweep_bar = CandleBarStub(now_ms + 60_000, 2649.0, 2652.0, 2648.0, 2649.5)
    new_state = update_cluster_state(buy_cluster, [sweep_bar], now_ms=now_ms + 120_000)
    assert new_state in ("SWEPT", "RECLAIMED")
    assert buy_cluster.swept_extreme == 2652.0


# ==============================================================================
# 5. SMC M1 FVG CONTINUATION (Rules 083 - 085, 164 - 165)
# ==============================================================================

def test_m1_fvg_continuation_positive_and_negative():
    """Rules 083-085, 164-165: Trend-aligned break + real FVG + closed retest."""
    cfg = M1ScalpConfig(min_closed_m1_bars=15)
    now_ms = 1_300_000

    # 1. Negative control: HTF conflicting
    candles = [CandleBarStub(60_000 * i, 2600.0, 2605.0, 2598.0, 2602.0) for i in range(1, 20)]
    quote = {"bid": 2602.0, "ask": 2602.5, "is_valid": True}
    res_neg = ScalpM1ModelService.evaluate_m1_fvg_continuation_candidate(
        candles, candles, quote, "BEARISH", "BEARISH", {"capital_usdt": 1000.0, "risk_pct": 0.25, "leverage": 50},
        now_ms=now_ms, config=cfg
    )
    assert res_neg.is_eligible is False

    # 2. Positive control: Bullish trend with FVG and retest
    c_list = [
        CandleBarStub(60_000, 2600.0, 2602.0, 2598.0, 2600.0),
        CandleBarStub(120_000, 2600.0, 2603.0, 2599.0, 2602.0),
        CandleBarStub(180_000, 2602.0, 2605.0, 2601.0, 2604.0),
        CandleBarStub(240_000, 2604.0, 2606.0, 2603.0, 2605.0),
        CandleBarStub(300_000, 2605.0, 2607.0, 2604.0, 2606.0),
        CandleBarStub(360_000, 2606.0, 2608.0, 2605.0, 2607.0),
        CandleBarStub(420_000, 2607.0, 2609.0, 2606.0, 2608.0),
        CandleBarStub(480_000, 2608.0, 2610.0, 2607.0, 2609.0),
        CandleBarStub(540_000, 2609.0, 2611.0, 2608.0, 2610.0),
        CandleBarStub(600_000, 2610.0, 2612.0, 2609.0, 2611.0),
        CandleBarStub(660_000, 2611.0, 2613.0, 2610.0, 2612.0),
        CandleBarStub(720_000, 2612.0, 2614.0, 2611.0, 2613.0),
        # Bar 13: High at 2615.0
        CandleBarStub(780_000, 2613.0, 2615.0, 2612.0, 2614.0),
        # Bar 14: Strong displacement candle up to 2625.0
        CandleBarStub(840_000, 2614.0, 2626.0, 2614.0, 2625.0),
        # Bar 15: Low at 2618.0 -> FVG between [2615.0, 2618.0]
        CandleBarStub(900_000, 2625.0, 2628.0, 2618.0, 2626.0),
        # Bar 16: Retest candle tapping [2615, 2618]
        CandleBarStub(960_000, 2626.0, 2627.0, 2616.0, 2620.0),
        CandleBarStub(1020_000, 2620.0, 2622.0, 2619.0, 2621.0),
    ]
    target_lvl = LiquidityLevel(
        id="target_high", price=2645.0, side="BUY_SIDE", source_kind="SWING",
        occurred_at_ms=100_000, known_at_ms=120_000, state="UNTOUCHED"
    )
    quote_pos = {"bid": 2620.0, "ask": 2620.5, "is_valid": True}
    res_pos = ScalpM1ModelService.evaluate_m1_fvg_continuation_candidate(
        c_list, c_list, quote_pos, "BULLISH", "BULLISH",
        {"capital_usdt": 1000.0, "risk_pct": 0.25, "leverage": 50},
        now_ms=1_100_000, config=cfg, context_levels=[target_lvl]
    )
    assert res_pos.is_eligible is True
    assert res_pos.model_id == ModelIdentifier.SMC_M1_FVG_CONTINUATION.value
    assert res_pos.direction == "LONG"
    assert res_pos.net_rr >= 2.0


# ==============================================================================
# 6. SMC M1 SWEEP RECLAIM (Rules 086 - 089, 166 - 167)
# ==============================================================================

def test_m1_sweep_reclaim_positive_and_negative():
    """Rules 086-089, 166-167: Reaction at confirmed cluster: sweep, reclaim close, target."""
    cfg = M1ScalpConfig(min_closed_m1_bars=10)
    now_ms = 1_000_000

    cluster = LiquidityCluster(
        id="c_sell_1", side="SELL_SIDE", cluster_type="EQUAL_LOWS",
        bottom=2600.0, top=2601.0, price_mid=2600.5,
        source_level_ids=["sl_1", "sl_2"], known_at_ms=500_000, state="UNSWEPT",
        swept_at_ms=None, swept_extreme=None, reclaimed_at_ms=None, volume_weight=1.0, quality_score=0.9
    )

    # 1. Negative control: prices never swept cluster bottom (2600.0)
    un_swept_bars = [
        CandleBarStub(60_000 * i, 2605.0, 2608.0, 2603.0, 2606.0) for i in range(1, 15)
    ]
    quote = {"bid": 2606.0, "ask": 2606.5, "is_valid": True}
    res_neg = ScalpM1ModelService.evaluate_m1_sweep_reclaim_candidate(
        cluster, un_swept_bars, quote, {"capital_usdt": 1000.0, "risk_pct": 0.25, "leverage": 50},
        now_ms=now_ms, config=cfg
    )
    assert res_neg.is_eligible is False

    # 2. Positive control: sweeps below 2600.0 (wick to 2595.0), then closed reclaim back above 2600.0
    swept_bars = [
        CandleBarStub(60_000 * i, 2605.0, 2607.0, 2603.0, 2604.0) for i in range(1, 10)
    ]
    # Sweep bar
    swept_bars.append(CandleBarStub(600_000, 2604.0, 2605.0, 2595.0, 2598.0))
    # Reclaim bar closing above 2600.0
    swept_bars.append(CandleBarStub(660_000, 2598.0, 2604.0, 2597.0, 2603.0))

    target_lvl = LiquidityLevel(
        id="target_high", price=2635.0, side="BUY_SIDE", source_kind="SWING",
        occurred_at_ms=100_000, known_at_ms=120_000, state="UNTOUCHED"
    )
    quote_pos = {"bid": 2603.0, "ask": 2603.5, "is_valid": True}
    res_pos = ScalpM1ModelService.evaluate_m1_sweep_reclaim_candidate(
        cluster, swept_bars, quote_pos, {"capital_usdt": 1000.0, "risk_pct": 0.25, "leverage": 50},
        now_ms=750_000, config=cfg, context_levels=[target_lvl]
    )
    assert res_pos.is_eligible is True
    assert res_pos.model_id == ModelIdentifier.SMC_M1_SWEEP_RECLAIM.value
    assert res_pos.direction == "LONG"
    assert res_pos.stop_loss <= 2595.0  # Protected below swept extreme


# ==============================================================================
# 7. MODEL REGISTRY FAIL-CLOSED (Rules 090 - 092, 168)
# ==============================================================================

def test_model_registry_fail_closed():
    """Rules 090-092, 168: All 5 models registered; unknown model fails closed."""
    for mid in [
        ModelIdentifier.SMC_M1_REVERSAL.value,
        ModelIdentifier.SMC_M1_CONTINUATION.value,
        ModelIdentifier.SMC_M1_FVG_CONTINUATION.value,
        ModelIdentifier.SMC_M1_SWEEP_RECLAIM.value,
        ModelIdentifier.NY_DAILY_RESEARCH.value,
    ]:
        spec = get_model_spec(mid)
        assert spec is not None
        assert spec.model_id == mid

    # Unknown model -> returns None (never fail-open)
    assert get_model_spec("UNKNOWN_UNREGISTERED_MODEL") is None

    assert get_session_family("UNKNOWN") == "UNSUPPORTED"
    assert requires_zone_lifecycle_authority("UNKNOWN") is True  # Fails safe to requiring authority


# ==============================================================================
# 8. CANDIDATE ROUTER & DEDUPLICATION (Rules 093 - 096, 169 - 170)
# ==============================================================================

def test_candidate_router_deduplication_and_conflict_resolution():
    """Rules 093-096, 169-170: Dedup same-level signals, deterministic conflict resolution."""
    # 1. Same direction & same price bucket -> dedup to best net_rr
    c1 = ScalpCandidate(
        is_eligible=True, model_id="SMC_M1_REVERSAL", direction="LONG", candidate_key="c1",
        planned_entry=2620.0, stop_loss=2610.0, take_profit=2650.0, net_rr=2.5, as_of_ms=100
    )
    c2 = ScalpCandidate(
        is_eligible=True, model_id="SMC_M1_SWEEP_RECLAIM", direction="LONG", candidate_key="c2",
        planned_entry=2620.5, stop_loss=2610.0, take_profit=2650.0, net_rr=2.8, as_of_ms=100
    )
    routed = evaluate_smc_opportunity_candidates([c1, c2], htf_bias="BULLISH")
    assert len(routed) == 1
    assert routed[0].candidate_key == "c2"  # Higher Net R:R picked

    # 2. Conflicting LONG and SHORT -> Aligned with HTF bias picked
    c_short = ScalpCandidate(
        is_eligible=True, model_id="SMC_M1_CONTINUATION", direction="SHORT", candidate_key="c_short",
        planned_entry=2640.0, stop_loss=2650.0, take_profit=2610.0, net_rr=3.0, as_of_ms=100
    )
    conflict_routed = evaluate_smc_opportunity_candidates([c1, c_short], htf_bias="BULLISH")
    assert len(conflict_routed) == 1
    assert conflict_routed[0].direction == "LONG"


# ==============================================================================
# 9. BITGET PUBLIC DEPTH VALIDATION (Rules 113 - 125)
# ==============================================================================

def test_bitget_public_depth_validation():
    """Rules 113-125: Orderbook quality validation (ordering, crossed book, freshness)."""
    now_ms = 1_700_000_000_000

    # 1. Valid depth
    bids = [[2650.0, 1.5], [2649.5, 2.0], [2649.0, 3.0]]
    asks = [[2650.5, 1.2], [2651.0, 2.5], [2651.5, 4.0]]
    q, p_bids, p_asks, spread, imb, det = validate_orderbook_levels(bids, asks, now_ms - 500, now_ms)
    assert q == "VALID"
    assert spread == 0.5
    assert len(p_bids) == 3
    assert len(p_asks) == 3

    # 2. Crossed book
    crossed_bids = [[2651.0, 1.0]]
    crossed_asks = [[2650.0, 1.0]]
    q_cr, _, _, _, _, _ = validate_orderbook_levels(crossed_bids, crossed_asks, now_ms - 500, now_ms)
    assert q_cr == "CROSSED"

    # 3. Stale book (> 5000ms old)
    q_stale, _, _, _, _, _ = validate_orderbook_levels(bids, asks, now_ms - 6000, now_ms)
    assert q_stale == "STALE"


# ==============================================================================
# 10. DAILY FREQUENCY GOAL EVALUATION (Rules 028 - 030, 126 - 140, 173 - 176)
# ==============================================================================

def test_daily_frequency_goal_progress(isolated_db: Session):
    """Rules 126-140, 173-176: Daily goal evaluation 0->1->2->3 fills, NY reserve, and shortfall."""
    policy = TradingPolicyService.get_or_create_policy(isolated_db)
    policy.entry_session_policy = "ALL_SESSIONS_WITH_NY_RESERVE"
    policy.max_daily_fills = 3
    policy.ny_min_fills = 1
    policy.reserve_ny_slot = True
    isolated_db.commit()

    # Time within NY session window
    win = TradingPolicyService.get_ny_session_window(policy, now_ms=1700000000000)
    in_win_ms = win["window_start_ms"] + 1800_000
    clock = FakeClock(in_win_ms)

    day_audit = crud.get_or_create_today_audit(isolated_db, clock=clock)

    # 0 fills in window -> SEARCHING
    day_audit.fills_count = 0
    isolated_db.commit()
    res0 = TradingPolicyService.evaluate_daily_frequency_goal(isolated_db, clock=clock)
    assert res0.status == "SEARCHING"
    assert res0.daily_fills == 0
    assert res0.remaining_daily_slots == 3

    # 1 fill in window -> SEARCHING
    day_audit.fills_count = 1
    isolated_db.commit()
    res1 = TradingPolicyService.evaluate_daily_frequency_goal(isolated_db, clock=clock)
    assert res1.status == "SEARCHING"
    assert res1.daily_fills == 1

    # 2 fills in window -> MINIMUM_MET
    day_audit.fills_count = 2
    isolated_db.commit()
    res2 = TradingPolicyService.evaluate_daily_frequency_goal(isolated_db, clock=clock)
    assert res2.status == "MINIMUM_MET"
    assert res2.daily_fills == 2

    # 3 fills in window -> CAP_REACHED
    day_audit.fills_count = 3
    isolated_db.commit()
    res3 = TradingPolicyService.evaluate_daily_frequency_goal(isolated_db, clock=clock)
    assert res3.status == "CAP_REACHED"
    assert res3.remaining_daily_slots == 0

    # Outside window with 0 fills -> SHORTFALL (Rule 127)
    after_win_clock = FakeClock(win["window_end_ms"] + 60_000)
    day_audit_after = crud.get_or_create_today_audit(isolated_db, clock=after_win_clock)
    day_audit_after.fills_count = 0
    isolated_db.commit()
    res_shortfall = TradingPolicyService.evaluate_daily_frequency_goal(isolated_db, clock=after_win_clock)
    assert res_shortfall.status == "SHORTFALL"
    assert res3.remaining_daily_slots == 0


# ==============================================================================
# 11. V16 CONFIG & MASTER FLAG ISOLATION (Rules 023 - 030, 186)
# ==============================================================================

def test_v16_config_and_master_flag_isolation(isolated_db: Session):
    """Rules 023-030, 186: Profile builder, config=None, invalid goals, and master flag off."""
    from services.scalp_m1_types import (
        build_v16_1_paper_profile,
        validate_daily_frequency_config,
    )

    # 1. Profile builder returns immutable profile with V16 enabled & 2/3 goals
    base_cfg = M1ScalpConfig()
    v16_profile = build_v16_1_paper_profile(base_cfg)
    assert v16_profile.v16_profile_enabled is True
    assert v16_profile.fvg_continuation_enabled is True
    assert v16_profile.sweep_reclaim_enabled is True
    assert v16_profile.multi_zone_hypotheses_enabled is True
    assert v16_profile.daily_min_goal == 2
    assert v16_profile.daily_max_cap == 3
    assert v16_profile.daily_ny_goal == 1

    # Base config must not be mutated
    assert base_cfg.multi_zone_hypotheses_enabled is False

    # 2. validate_daily_frequency_config rejects invalid goals
    with pytest.raises(ValueError, match="INVALID_DAILY_GOAL_CONFIG"):
        validate_daily_frequency_config(type("Cfg", (), {"daily_min_goal": 5, "daily_max_cap": 3, "daily_ny_goal": 1})())

    with pytest.raises(ValueError, match="INVALID_DAILY_GOAL_CONFIG"):
        validate_daily_frequency_config(type("Cfg", (), {"daily_min_goal": -1, "daily_max_cap": 3, "daily_ny_goal": 1})())

    with pytest.raises(ValueError, match="INVALID_DAILY_NY_GOAL_CONFIG"):
        validate_daily_frequency_config(type("Cfg", (), {"daily_min_goal": 2, "daily_max_cap": 3, "daily_ny_goal": 4})())

    # 3. config=None does not crash and defaults safely
    c_list = [CandleBarStub(60_000 * i, 2600.0, 2605.0, 2598.0, 2602.0) for i in range(1, 20)]
    quote = {"bid": 2602.0, "ask": 2602.5, "is_valid": True}
    res_none = ScalpM1ModelService.evaluate_m1_fvg_continuation_candidate(
        c_list, c_list, quote, "BEARISH", "BEARISH", {"capital_usdt": 1000.0, "risk_pct": 0.25, "leverage": 50},
        now_ms=1_500_000, config=None
    )
    assert res_none.is_eligible is False

    # 4. Master flag disabled blocks evaluation
    cfg_disabled = M1ScalpConfig(fvg_continuation_enabled=False)
    res_dis = ScalpM1ModelService.evaluate_m1_fvg_continuation_candidate(
        c_list, c_list, quote, "BULLISH", "BULLISH", {"capital_usdt": 1000.0, "risk_pct": 0.25, "leverage": 50},
        now_ms=1_500_000, config=cfg_disabled
    )
    assert res_dis.is_eligible is False
    assert res_dis.reason_code == "MODEL_DISABLED"


# ==============================================================================
# 12. FVG CONTINUATION NEGATIVE CONTROLS (Rules 046 - 060, 187)
# ==============================================================================

def test_m1_fvg_continuation_negatives_comprehensive():
    """Rule 187: Break missing, weak displacement, future leak, FVG invalidated, conflict, wrong side target."""
    from services.scalp_m1_model_service import validate_evidence_timeline
    cfg = M1ScalpConfig(min_closed_m1_bars=15)
    now_ms = 1_100_000

    # 1. Break missing: candles rise without breaking any reference level
    c_no_break = [
        CandleBarStub(60_000 * i, 2600.0 + (i * 0.1), 2601.0 + (i * 0.1), 2599.0 + (i * 0.1), 2600.5 + (i * 0.1))
        for i in range(1, 20)
    ]
    quote = {"bid": 2602.0, "ask": 2602.5, "is_valid": True}
    res1 = ScalpM1ModelService.evaluate_m1_fvg_continuation_candidate(
        c_no_break, c_no_break, quote, "BULLISH", "BULLISH",
        {"capital_usdt": 1000.0, "risk_pct": 0.25, "leverage": 50},
        now_ms=now_ms, config=cfg
    )
    assert res1.is_eligible is False
    assert res1.reason_code in ("M1_BREAK_MISSING", "WAITING_M1_STRUCTURE", "DISPLACEMENT_WEAK")

    # 2. HTF / H1 conflict: BULLISH vs BEARISH
    res2 = ScalpM1ModelService.evaluate_m1_fvg_continuation_candidate(
        c_no_break, c_no_break, quote, "BULLISH", "BEARISH",
        {"capital_usdt": 1000.0, "risk_pct": 0.25, "leverage": 50},
        now_ms=now_ms, config=cfg
    )
    assert res2.is_eligible is False
    assert res2.reason_code == "HTF_H1_CONFLICT"

    # 3. Future candle leak detected by validate_evidence_timeline
    future_ev = [
        {"id": "ev_1", "occurred_at_ms": 100_000, "known_at_ms": 120_000},
        {"id": "ev_future", "occurred_at_ms": 150_000, "known_at_ms": 2_000_000},  # known > now_ms (1_100_000)
    ]
    is_valid_tl, issues = validate_evidence_timeline(future_ev, now_ms=now_ms)
    assert is_valid_tl is False
    assert any("future leak" in iss for iss in issues)

    # 4. Target on wrong side: SHORT direction but target price > entry price
    target_wrong_side = LiquidityLevel(
        id="t_wrong", price=2650.0, side="BUY_SIDE", source_kind="SWING",
        occurred_at_ms=100_000, known_at_ms=120_000, state="UNTOUCHED"
    )
    res_wrong_target = ScalpM1ModelService.evaluate_m1_fvg_continuation_candidate(
        c_no_break, c_no_break, quote, "BEARISH", "BEARISH",
        {"capital_usdt": 1000.0, "risk_pct": 0.25, "leverage": 50},
        now_ms=now_ms, config=cfg, context_levels=[target_wrong_side]
    )
    assert res_wrong_target.is_eligible is False


# ==============================================================================
# 13. SWEEP RECLAIM NEGATIVE CONTROLS (Rules 061 - 074, 188)
# ==============================================================================

def test_m1_sweep_reclaim_negatives_comprehensive():
    """Rule 188: Sweep before cluster known, reclaim not closed, missing micro-break, invalid side, ACCEPTED_BEYOND, consumed."""
    cfg = M1ScalpConfig(min_closed_m1_bars=10)
    now_ms = 1_000_000

    # 1. Sweep before cluster known: cluster known at 800_000, but sweep occurred at 600_000
    cluster_future_known = LiquidityCluster(
        id="c_future", side="SELL_SIDE", cluster_type="EQUAL_LOWS",
        bottom=2600.0, top=2601.0, price_mid=2600.5,
        source_level_ids=["sl_1"], known_at_ms=800_000, state="UNSWEPT",
        volume_weight=1.0, quality_score=0.9
    )
    swept_bars = [
        CandleBarStub(60_000 * i, 2605.0, 2607.0, 2603.0, 2604.0) for i in range(1, 10)
    ]
    swept_bars.append(CandleBarStub(600_000, 2604.0, 2605.0, 2595.0, 2598.0))  # Sweep at 600k < known 800k
    swept_bars.append(CandleBarStub(660_000, 2598.0, 2604.0, 2597.0, 2603.0))  # Reclaim
    quote = {"bid": 2603.0, "ask": 2603.5, "is_valid": True}

    res_early = ScalpM1ModelService.evaluate_m1_sweep_reclaim_candidate(
        cluster_future_known, swept_bars, quote, {"capital_usdt": 1000.0, "risk_pct": 0.25, "leverage": 50},
        now_ms=750_000, config=cfg
    )
    assert res_early.is_eligible is False

    # 2. Unknown / invalid cluster side
    cluster_invalid_side = LiquidityCluster(
        id="c_inv", side="UNKNOWN_SIDE", cluster_type="EQUAL_LOWS",
        bottom=2600.0, top=2601.0, price_mid=2600.5,
        source_level_ids=["sl_1"], known_at_ms=100_000, state="UNSWEPT",
        volume_weight=1.0, quality_score=0.9
    )
    res_inv = ScalpM1ModelService.evaluate_m1_sweep_reclaim_candidate(
        cluster_invalid_side, swept_bars, quote, {"capital_usdt": 1000.0, "risk_pct": 0.25, "leverage": 50},
        now_ms=now_ms, config=cfg
    )
    assert res_inv.is_eligible is False
    assert res_inv.reason_code == "CLUSTER_INVALID"

    # 3. ACCEPTED_BEYOND state: price accepted beyond cluster, no reclaim
    cluster_accepted = LiquidityCluster(
        id="c_acc", side="SELL_SIDE", cluster_type="EQUAL_LOWS",
        bottom=2600.0, top=2601.0, price_mid=2600.5,
        source_level_ids=["sl_1"], known_at_ms=100_000, state="ACCEPTED_BEYOND",
        volume_weight=1.0, quality_score=0.9
    )
    res_acc = ScalpM1ModelService.evaluate_m1_sweep_reclaim_candidate(
        cluster_accepted, swept_bars, quote, {"capital_usdt": 1000.0, "risk_pct": 0.25, "leverage": 50},
        now_ms=now_ms, config=cfg
    )
    assert res_acc.is_eligible is False
    assert res_acc.reason_code == "ACCEPTED_BEYOND"

    # 4. CONSUMED cluster state
    cluster_consumed = LiquidityCluster(
        id="c_con", side="SELL_SIDE", cluster_type="EQUAL_LOWS",
        bottom=2600.0, top=2601.0, price_mid=2600.5,
        source_level_ids=["sl_1"], known_at_ms=100_000, state="CONSUMED",
        volume_weight=1.0, quality_score=0.9
    )
    res_con = ScalpM1ModelService.evaluate_m1_sweep_reclaim_candidate(
        cluster_consumed, swept_bars, quote, {"capital_usdt": 1000.0, "risk_pct": 0.25, "leverage": 50},
        now_ms=now_ms, config=cfg
    )
    assert res_con.is_eligible is False
    assert res_con.reason_code == "CLUSTER_INVALID"


# ==============================================================================
# 14. ATOMIC DAILY CAP PROTECTION (Rules 008, 131, 190)
# ==============================================================================

def test_daily_cap_atomic_protection(isolated_db: Session):
    """Rules 008, 131, 190: Atomic daily max cap blocks 4th entry attempt under serialization lock."""
    from services.scalp_watcher_orchestrator import scalp_watcher_orchestrator
    from services.strategy_service import strategy_service
    from domain_calculator import calculate_risk_reward

    strategy_service.set_auto_state(isolated_db, True)
    cfg = M1ScalpConfig(daily_max_cap=3)
    clock = FakeClock(1_700_000_000_000)

    # Set day audit to 3 fills
    day_audit = crud.get_or_create_today_audit(isolated_db, clock=clock)
    day_audit.fills_count = 3
    isolated_db.commit()

    calc = calculate_risk_reward(
        direction="LONG", entry=2620.0, sl=2615.0, tp=2645.0,
        capital=1000.0, risk_pct=0.25, leverage=50, margin_mode="ISOLATED"
    )

    cand = ScalpCandidate(
        is_eligible=True,
        model_id=ModelIdentifier.SMC_M1_FVG_CONTINUATION.value,
        direction="LONG",
        candidate_key="cand_cap_test_1",
        symbol="XAUUSDT",
        planned_entry=2620.0,
        stop_loss=2615.0,
        take_profit=2645.0,
        net_rr=calc.net_rr,
        calculation=calc,
    )

    quote = {"bid": 2620.0, "ask": 2620.5, "is_valid": True, "feed_healthy": True}
    armed = scalp_watcher_orchestrator._arm_paper_candidate(
        isolated_db, cand, now_ms=1_700_000_000_000, clock=clock, quote=quote,
        policy_settings={"capital_usdt": 1000.0}, config=cfg, auto_enabled=True
    )
    assert armed is None

    # Check opportunity funnel recorded DAILY_CAP_REACHED
    rec = isolated_db.query(models.OpportunityFunnelRecord).filter(
        models.OpportunityFunnelRecord.opportunity_id == "opp_cand_cap_test_1"
    ).first()
    assert rec is not None
    assert "DAILY_CAP_REACHED" in (rec.reason_codes_json or "")


# ==============================================================================
# 15. DAILY SHORTFALL SUMMARY CATEGORIES (Rules 136, 175)
# ==============================================================================

def test_daily_shortfall_summary_categories(isolated_db: Session):
    """Rule 136: Categorizes shortfall reasons across 9 canonical categories and identifies primary cause."""
    from services.trading_policy_service import summarize_daily_shortfall, SHORTFALL_CATEGORIES

    clock = FakeClock(1_700_000_000_000)
    # Feed test records across different categories
    mock_records = [
        {"opportunity_id": "opp_1", "reason_codes": ["M1_BREAK_MISSING"]},
        {"opportunity_id": "opp_2", "reason_codes": ["DISPLACEMENT_WEAK"]},
        {"opportunity_id": "opp_3", "reason_codes": ["SPREAD_TOO_WIDE"]},
        {"opportunity_id": "opp_4", "reason_codes": ["HIGH_IMPACT_NEWS"]},
        {"opportunity_id": "opp_5", "reason_codes": ["ACTIVE_POSITION_EXISTS"]},
        {"opportunity_id": "opp_6", "reason_codes": ["M1_MICRO_BREAK_MISSING"]},
    ]

    summary = summarize_daily_shortfall(
        isolated_db, now_ms=1_700_000_000_000, clock=clock, daily_min_goal=2, funnel_records=mock_records
    )
    assert summary["has_shortfall"] is True
    assert summary["distinct_opportunities_impacted"] == 6
    assert summary["primary_category"] == "model_confirmation"  # 3 opps in model_confirmation
    for cat in SHORTFALL_CATEGORIES:
        assert cat in summary["category_breakdown"]


# ==============================================================================
# 16. DAILY STATUS API (Rule 138)
# ==============================================================================

def test_v16_daily_status_api():
    """Rule 138: Lightweight daily status API returns canonical payload."""
    from fastapi.testclient import TestClient
    from main import app

    client = TestClient(app)
    res = client.get("/api/scalp/daily-status")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "SUCCESS"
    assert "trading_day_id" in data
    assert "fills_count" in data
    assert "min_goal" in data
    assert "cap" in data
    assert "ny_fills" in data
    assert "reservation_state" in data
    assert "search_state" in data
    assert "primary_reason" in data
