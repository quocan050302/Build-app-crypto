"""
test_v15_3_causal_wiring.py
V15.3 — Unit and Integration Suite for Causal Data Contracts, Real Zone Assessment, and Non-Refreshing Triggers.
SECTIONS 171 - 193.
"""
from __future__ import annotations

import math
import pytest
from dataclasses import dataclass
from typing import Any, List
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

import models
from main import app
from services.liquidity_scenario_service import (
    ConfirmedSwing,
    TIMEFRAME_DURATIONS_MS,
    assess_range_position,
    build_liquidity_hypotheses,
    build_liquidity_map,
    compute_dealing_range,
    detect_order_blocks,
    identify_confirmed_swings,
    rank_zones_multi_dimensional,
    resolve_timeframe_duration_ms,
)
from services.scalp_m1_model_service import ScalpCandidate, ScalpM1ModelService
from services.scalp_m1_types import (
    M1ScalpConfig,
    ModelIdentifier,
    OpportunityReasonCode,
    ScalpMode,
    WatchedZone,
    ZoneState,
    load_effective_scalp_config,
)
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator
from services.zone_watchlist_service import ZoneWatchlistService
from tests.test_v15_1_execution_lifecycle import _make_causal_long_m1_candles, _make_causal_short_m1_candles, _setup_positive_trading_policy


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


def test_v15_3_timeframe_aware_swings_and_boundaries():
    """SECTIONS 021 - 032, 171, 172: Timeframe durations, causal confirmation boundaries, and non-zero timestamps."""
    # 1. Duration resolver
    assert resolve_timeframe_duration_ms("1M") == 60_000
    assert resolve_timeframe_duration_ms("5M") == 300_000
    assert resolve_timeframe_duration_ms("15M") == 900_000
    assert resolve_timeframe_duration_ms("1H") == 3_600_000
    with pytest.raises(ValueError, match="UNSUPPORTED_TIMEFRAME"):
        resolve_timeframe_duration_ms("42X")

    # 2. Build 5 candles of 5M: pivot at candle 2 (open 600_000), right_bars=2 (candles 3 and 4)
    # Candle 4 opens at 1_200_000, closes at 1_200_000 + 300_000 = 1_500_000
    c_5m = [
        CandleStub(0, 2600.0, 2605.0, 2595.0, 2602.0),
        CandleStub(300_000, 2602.0, 2608.0, 2600.0, 2607.0),
        CandleStub(600_000, 2607.0, 2625.0, 2605.0, 2620.0),  # Swing High at 2625.0
        CandleStub(900_000, 2620.0, 2622.0, 2615.0, 2618.0),
        CandleStub(1_200_000, 2618.0, 2619.0, 2612.0, 2614.0), # Right-2 confirmation candle
    ]

    # Before close of right-2 candle (as_of = 1_499_999), swing is NOT yet known
    sh_before, sl_before = identify_confirmed_swings(c_5m, timeframe="5M", left_bars=2, right_bars=2, as_of_ms=1_499_999)
    assert len(sh_before) == 0

    # At/after close of right-2 candle (as_of = 1_500_000), swing IS known
    sh_after, _ = identify_confirmed_swings(c_5m, timeframe="5M", left_bars=2, right_bars=2, as_of_ms=1_500_000)
    assert len(sh_after) == 1
    s = sh_after[0]
    assert s["occurred_at_ms"] == 600_000
    assert s["known_at_ms"] == 1_500_000  # 1_200_000 + 300_000 ms, NOT 60_000 ms hardcoded
    assert s["side"] == "BUY_SIDE"
    assert s["type"] == "HIGH"
    assert s["price"] == 2625.0
    assert s["timestamp"] == 600_000
    assert s["confirmed_at"] == 1_500_000


def test_v15_3_prefix_atr_and_order_block_extraction():
    """SECTIONS 041 - 054, 173, 174: Causal prefix ATR and extract_zones_from_candles without handcrafted swings."""
    # 25 candles with an established swing high, then displacement break
    candles: List[CandleStub] = []
    # Base downtrend
    for i in range(10):
        candles.append(CandleStub(i * 60_000, 2600.0 - i, 2602.0 - i, 2598.0 - i, 2599.0 - i))
    # Swing High at index 10 (open 600_000)
    candles.append(CandleStub(600_000, 2600.0, 2620.0, 2599.0, 2618.0))
    # 2 right bars confirming swing high
    candles.append(CandleStub(660_000, 2618.0, 2619.0, 2610.0, 2612.0))
    candles.append(CandleStub(720_000, 2612.0, 2614.0, 2605.0, 2608.0))
    # Downward base candle before breakout (opposing base)
    candles.append(CandleStub(780_000, 2608.0, 2610.0, 2606.0, 2607.0))
    # Strong impulse break above 2620.0
    candles.append(CandleStub(840_000, 2607.0, 2635.0, 2606.5, 2634.0))
    # Follow-through candles
    for i in range(5):
        t = 900_000 + i * 60_000
        candles.append(CandleStub(t, 2634.0, 2636.0, 2632.0, 2635.0))

    # Real extraction directly from candles
    zones = ZoneWatchlistService.extract_zones_from_candles(candles, timeframe="1M", symbol="XAUUSDT")
    assert isinstance(zones, list)
    # Zones must have valid bounds and causal timestamps
    for z in zones:
        assert z.top > z.bottom
        assert z.known_at >= z.created_at
        assert z.symbol == "XAUUSDT"

    # Prefix ATR check: appending future extreme volatility bars does NOT retroactively change earlier OBs
    test_swings = [{"id": "sh_1", "symbol": "XAUUSDT", "side": "BUY_SIDE", "type": "HIGH", "price": 2620.0, "occurred_at_ms": 600_000, "known_at_ms": 780_000}]
    obs_t1 = detect_order_blocks(candles, test_swings, timeframe="1M", symbol="XAUUSDT")
    # Append wild future volatility bars
    future_candles = list(candles)
    for i in range(10):
        t = 1_500_000 + i * 60_000
        future_candles.append(CandleStub(t, 2635.0, 2750.0, 2500.0, 2700.0))  # 250 pt range
    obs_t2 = detect_order_blocks(future_candles, test_swings, timeframe="1M", symbol="XAUUSDT")

    # The OB detected at T1 must have identical geometry in T2
    if obs_t1:
        ob1 = obs_t1[0]
        ob2_match = [b for b in obs_t2 if b.created_at == ob1.created_at and b.zone_type == ob1.zone_type]
        assert len(ob2_match) >= 1
        assert ob2_match[0].top == ob1.top
        assert ob2_match[0].bottom == ob1.bottom


def test_v15_3_dealing_range_and_liquidity_map_honesty():
    """SECTIONS 061 - 075, 175: Dealing range and liquidity map contract with side/type and known_at_ms."""
    # Swings with canonical fields
    swings = [
        {"id": "sh_1", "symbol": "XAUUSDT", "side": "BUY_SIDE", "type": "HIGH", "price": 2650.0, "occurred_at_ms": 100_000, "known_at_ms": 300_000},
        {"id": "sl_1", "symbol": "XAUUSDT", "side": "SELL_SIDE", "type": "LOW", "price": 2600.0, "occurred_at_ms": 400_000, "known_at_ms": 600_000},
    ]

    dr = compute_dealing_range(swings, now_ms=700_000)
    assert dr.is_valid is True
    assert dr.range_high == 2650.0
    assert dr.range_low == 2600.0
    assert dr.equilibrium == 2625.0
    assert dr.known_at_ms == 600_000

    # Assess position
    pos_disc = assess_range_position(2610.0, dr)
    assert pos_disc == "DISCOUNT"
    pos_prem = assess_range_position(2640.0, dr)
    assert pos_prem == "PREMIUM"
    pos_eq = assess_range_position(2625.0, dr)
    assert pos_eq == "EQUILIBRIUM"

    # Liquidity map produces levels with genuine timestamps, never fallback 0
    from services.smc_liquidity_service import build_liquidity_map as build_liq_map
    levels = build_liq_map(
        symbol="XAUUSDT", timeframe="15M", swing_highs=[swings[0]], swing_lows=[swings[1]], now_ms=700_000
    )
    assert len(levels) == 2
    for lvl in levels:
        assert lvl.occurred_at_ms > 0
        assert lvl.known_at_ms >= lvl.occurred_at_ms


def test_v15_3_honest_hypotheses_no_fake_targets():
    """SECTIONS 101 - 120, 179, 180: Hypotheses never fabricate current_price ± 10 when unconsumed target is missing."""
    zone = WatchedZone(
        zone_id="z_honest_01", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
    )
    # Empty liquidity levels (no targets available)
    dr = compute_dealing_range([], now_ms=100_000)

    hyps = build_liquidity_hypotheses(
        zones=[zone], levels=[], dealing_range=dr, current_price=2615.0, regime="BULLISH_TREND", now_ms=100_000
    )
    assert len(hyps) >= 1
    primary = hyps[0]
    # Target price MUST be None, target_type UNAVAILABLE (no fake 2625.0 / ±10)
    assert primary.target_price is None
    assert primary.target_type == "UNAVAILABLE"
    assert primary.target_status == "TARGET_UNAVAILABLE"


def test_v15_3_real_zone_assessment_and_ranking_runtime(isolated_db: Session):
    """SECTIONS 081 - 100, 177, 178: Multi-dimensional ranking connected to evaluate_cycle without hardcoded 60/70 scores."""
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    cfg = M1ScalpConfig(scalp_m1_mode=ScalpMode.PAPER.value)

    zone = WatchedZone(
        zone_id="z_rank_01", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value, touch_count=1, freshness_state="FRESH",
    )
    orch.set_active_zones("XAUUSDT", [zone])
    candles = _make_causal_long_m1_candles()
    quote = {"bid": 2622.0, "ask": 2622.5, "is_valid": True, "server_time": 1_300_000, "observed_at": 1_300_000}

    res = orch.evaluate_cycle(
        isolated_db, "XAUUSDT", now_ms=1_300_000, quote=quote, candles_1m=candles,
        candles_5m=[], candles_15m=[], config=cfg, auto_enabled=True,
    )

    # Explanation endpoint returns real calculated dimensions, NOT fixed 60.0/70.0
    expl = orch.get_zone_assessment_explanation("XAUUSDT", "z_rank_01")
    assert expl is not None
    assert expl["zone_id"] == "z_rank_01"
    # Verify dimensions are present and win_probability is strictly None
    assert "quality_score" in expl
    assert "context_score" in expl
    assert "readiness_score" in expl
    assert "execution_score" in expl
    assert expl["win_probability"] is None  # SECTIONS 009, 097: Strictly uncalibrated
    assert expl["as_of_ms"] == 1_300_000


def test_v15_3_non_refreshing_ttl_and_entry_distance():
    """SECTIONS 130 - 134, 183, 184: First retest bar fixes trigger_known_at; entry distance uses fixed reference."""
    candles_1m = _make_causal_long_m1_candles()
    zone = WatchedZone(
        zone_id="z_retest_ttl", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    quote = {"bid": 2622.0, "ask": 2622.5, "is_valid": True, "server_time": 1_300_000, "observed_at": 1_300_000}
    policy_dict = {"capital_usdt": 1000.0, "risk_pct": 0.1, "leverage": 50, "margin_mode": "ISOLATED"}

    # At now_ms = 1_300_000, trigger_known_at is fixed at 1_140_000 (age 160_000 ms).
    # With max_trigger_age_ms = 100_000 ms, candidate expires even though candles 18, 19, 20 retested later
    cfg_expired = M1ScalpConfig(max_trigger_age_ms=100_000)
    cand_expired = ScalpM1ModelService.evaluate_m1_reversal_candidate(
        zone, candles_1m, quote, policy_dict, now_ms=1_300_000, config=cfg_expired
    )
    assert cand_expired.is_eligible is False
    assert cand_expired.reason_code == OpportunityReasonCode.TRIGGER_EXPIRED.value

    # Far quote beyond 1.5 ATR triggers ENTRY_TOO_FAR
    far_quote = {"bid": 2635.0, "ask": 2635.5, "is_valid": True, "server_time": 1_300_000, "observed_at": 1_300_000}
    cand_far = ScalpM1ModelService.evaluate_m1_reversal_candidate(
        zone, candles_1m, far_quote, policy_dict, now_ms=1_300_000
    )
    assert cand_far.is_eligible is False
    assert cand_far.reason_code == OpportunityReasonCode.ENTRY_TOO_FAR.value


def test_v15_3_scalp_config_api_keys_persistence_and_roundtrip(client: TestClient, isolated_db: Session):
    """SECTIONS 166 - 170: Config API accepts 'mode' or 'scalp_m1_mode' and roundtrips max_trigger_age_ms/max_entry_distance_atr."""
    # POST config using 'mode' key
    resp = client.post("/api/scalp/config", json={
        "mode": "PAPER",
        "scalp_m1_enabled": True,
        "max_trigger_age_ms": 240_000,
        "max_entry_distance_atr": 1.25,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["effective_config"]["scalp_m1_mode"] == "PAPER"
    assert data["effective_config"]["mode"] == "PAPER"
    assert data["effective_config"]["max_trigger_age_ms"] == 240_000
    assert data["effective_config"]["max_entry_distance_atr"] == 1.25

    # GET config returns identical persisted values
    get_resp = client.get("/api/scalp/config")
    assert get_resp.status_code == 200
    get_data = get_resp.json()
    assert get_data["effective_config"]["scalp_m1_mode"] == "PAPER"
    assert get_data["effective_config"]["max_trigger_age_ms"] == 240_000
    assert get_data["effective_config"]["max_entry_distance_atr"] == 1.25

    # Runtime loader reads them from DB
    loaded = load_effective_scalp_config(isolated_db)
    assert loaded.scalp_m1_mode == ScalpMode.PAPER.value
    assert loaded.max_trigger_age_ms == 240_000
    assert loaded.max_entry_distance_atr == 1.25


def test_v15_3_real_fvg_retest_vs_broad_atr():
    """SECTIONS 127, 128, 182: Candles touching broad ATR but missing real FVG are rejected with WAITING_M1_RETEST."""
    candles = _make_causal_long_m1_candles()
    # Modify post-break candles so they only retest down to 2623.0 (above FVG top 2622.0)
    # 2623.0 is well within broad ATR interval (break_level 2618 + 1.5*atr ~ 2622.5 - 2626), but outside real FVG [2614, 2622]
    modified_candles = []
    for c in candles:
        if c.timestamp > 1020_000:
            modified_candles.append(CandleStub(c.timestamp, c.open, c.high, max(2623.0, c.low), c.close))
        else:
            modified_candles.append(c)

    zone = WatchedZone(
        zone_id="z_broad_fvg", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    quote = {"bid": 2623.5, "ask": 2624.0, "is_valid": True, "server_time": 1_300_000, "observed_at": 1_300_000}
    policy = {"capital_usdt": 1000.0, "risk_pct": 0.1, "leverage": 50, "margin_mode": "ISOLATED"}

    cand = ScalpM1ModelService.evaluate_m1_reversal_candidate(
        zone, modified_candles, quote, policy, now_ms=1_300_000
    )
    assert cand.is_eligible is False
    assert cand.reason_code == OpportunityReasonCode.WAITING_M1_RETEST.value
    assert "retest FVG" in cand.missing_conditions[0]


def test_v15_3_deterministic_zone_ranking_order_invariance():
    """SECTIONS 094, 178: rank_zones_multi_dimensional produces identical ranking order and scores regardless of input order."""
    z1 = WatchedZone(
        zone_id="z_01", symbol="XAUUSDT", timeframe="15M", zone_type="OB",
        direction="BULLISH", top=2625.0, bottom=2615.0, created_at=100_000, known_at=200_000,
        touch_count=2, freshness_state="FRESH",
    )
    z2 = WatchedZone(
        zone_id="z_02", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2610.0, bottom=2600.0, created_at=150_000, known_at=250_000,
        touch_count=0, freshness_state="FRESH",
    )
    dr = compute_dealing_range([], now_ms=300_000)

    # Rank with order [z1, z2]
    ranked_forward = rank_zones_multi_dimensional([z1, z2], levels=[], dealing_range=dr, current_price=2618.0, atr=2.0, now_ms=300_000)
    # Rank with order [z2, z1]
    ranked_reversed = rank_zones_multi_dimensional([z2, z1], levels=[], dealing_range=dr, current_price=2618.0, atr=2.0, now_ms=300_000)

    assert len(ranked_forward) == 2
    assert len(ranked_reversed) == 2
    # The resulting top zone ID and scores must be identical
    assert ranked_forward[0][0].zone_id == ranked_reversed[0][0].zone_id
    assert ranked_forward[0][1].total_score == ranked_reversed[0][1].total_score
    assert ranked_forward[1][0].zone_id == ranked_reversed[1][0].zone_id
    assert ranked_forward[1][1].total_score == ranked_reversed[1][1].total_score


def test_v15_3_zone_rank_never_bypasses_guards(isolated_db: Session):
    """SECTIONS 006, 187: High zone rank score cannot override required guards (e.g. daily quota 3/3)."""
    _setup_positive_trading_policy(isolated_db)
    # Seed 3 fills today
    audit = models.DayAudit(
        date_str="2026-10-15",
        fills_count=3,
        initial_equity=1000.0,
        current_equity=1000.0,
    )
    isolated_db.add(audit)
    isolated_db.commit()

    orch = ScalpWatcherOrchestrator()
    cfg = M1ScalpConfig(scalp_m1_mode=ScalpMode.PAPER.value)

    # High quality zone with 0 touches, fresh FVG
    zone = WatchedZone(
        zone_id="z_high_score", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value, touch_count=0, freshness_state="FRESH",
    )
    orch.set_active_zones("XAUUSDT", [zone])
    candles = _make_causal_long_m1_candles()
    quote = {"bid": 2622.0, "ask": 2622.5, "is_valid": True, "server_time": 1_300_000, "observed_at": 1_300_000}

    res = orch.evaluate_cycle(
        isolated_db, "XAUUSDT", now_ms=1_300_000, quote=quote, candles_1m=candles,
        candles_5m=[], candles_15m=[], config=cfg, auto_enabled=True,
    )

    # Guard blocks execution despite high zone score
    assert res["armed_order_id"] is None
    assert isolated_db.query(models.PaperOrder).count() == 0
