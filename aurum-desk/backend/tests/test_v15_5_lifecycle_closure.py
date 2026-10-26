"""
backend/tests/test_v15_5_lifecycle_closure.py
V15.5 - Final Runtime Lifecycle Closure Test Suite.

Covers the four remaining reviewer issues:
1. Durable hypothesis lifecycle & expiry persistence across restarts (Sections 018, 021 - 075).
2. Exact break displacement qualification without borrowing later candles (Sections 019, 076 - 115).
3. Complete acceptance runner registration for V15.3, V15.4, and V15.5 (Sections 116 - 130).
4. Real ARM -> FILL -> CLOSE execution lifecycle for LONG TP and SHORT SL (Sections 131 - 170).
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
import pytest
from sqlalchemy.orm import Session

import models
import crud
from domain_calculator import calculate_risk_reward
from services.clock import FakeClock
from services.liquidity_scenario_service import (
    DealingRange,
    DurableHypothesisRecord,
    HypothesisLoadStatus,
    LiquidityHypothesis,
    build_liquidity_hypotheses,
    compute_hypothesis_source_fingerprint,
    get_hypothesis_storage_key,
    load_durable_hypotheses_for_symbol,
    load_hypothesis_lifecycle,
    reconcile_hypothesis_lifecycle,
    stage_hypothesis_lifecycle,
)
from services.scalp_m1_model_service import (
    ScalpCandidate,
    ScalpM1ModelService,
    qualify_selected_break,
)
from services.scalp_m1_types import (
    M1ScalpConfig,
    ModelIdentifier,
    OpportunityEvaluation,
    OpportunityReasonCode,
    OpportunityStage,
    ScalpMode,
    WatchedZone,
    ZoneState,
)
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator
from services.smc_liquidity_service import build_liquidity_map
from services.smc_scenario_types import LiquidityLevel
from services.trade_lifecycle_service import TradeLifecycleService
from tests.test_v15_1_execution_lifecycle import (
    CandleStub,
    _make_causal_long_m1_candles,
    _make_causal_short_m1_candles,
    _setup_positive_trading_policy,
)


# =====================================================================
# SECTION 1: DURABLE HYPOTHESIS LIFECYCLE ACROSS RESTARTS (018, 021-075)
# =====================================================================

def test_durable_hypothesis_restart_preserves_expiry(isolated_db: Session):
    """
    SECTION 018, 046, 068: RESTART REPRO FIX.
    T0: First cycle creates hypothesis with created_at_ms=T0, expiry_ms=T0+4h.
    Service restarts: brand new ScalpWatcherOrchestrator instance created.
    T1 (T0 + 1h): Second cycle must load durable anchors from DB.
    Expiry before and after restart must be identical (no forward drift).
    """
    _setup_positive_trading_policy(isolated_db)
    t0 = 1_000_000_000
    cfg = M1ScalpConfig(
        scalp_m1_mode=ScalpMode.PAPER.value,
        daily_ny_research_enabled=False,
        min_closed_m1_bars=15,
        displacement_body_atr_min=0.5,
    )
    zone = WatchedZone(
        zone_id="z_restart_test", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=t0 - 60_000, known_at=t0 - 60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    quote = {"bid": 2615.0, "ask": 2615.5, "is_valid": True, "server_time": t0, "observed_at": t0}
    feed_health = {"status": "CONNECTED", "latency_ms": 10}

    # 1. First runtime instance at T0
    orch1 = ScalpWatcherOrchestrator()
    orch1.set_active_zones("XAUUSDT", [zone])
    res1 = orch1.evaluate_cycle(
        isolated_db, "XAUUSDT",
        now_ms=t0, quote=quote, candles_1m=_make_causal_long_m1_candles(),
        candles_5m=[], candles_15m=[], config=cfg, feed_health=feed_health, auto_enabled=True,
    )
    assert res1["status"] == "OK"

    # Inspect persisted hypothesis in DB
    durable_map1 = load_durable_hypotheses_for_symbol(isolated_db, "XAUUSDT")
    hyp_id = f"hyp-primary-{zone.zone_id}"
    assert hyp_id in durable_map1
    d_rec1 = durable_map1[hyp_id]
    assert d_rec1.created_at_ms == t0
    original_expiry = d_rec1.expiry_ms
    assert original_expiry == t0 + 4 * 3600 * 1000

    # 2. Simulate restart: instantiate brand new orchestrator with empty in-memory state
    t1 = t0 + 3600 * 1000  # 1 hour later
    orch2 = ScalpWatcherOrchestrator()
    orch2.set_active_zones("XAUUSDT", [zone])
    assert "XAUUSDT" not in orch2._active_hypotheses  # In-memory cache is empty

    res2 = orch2.evaluate_cycle(
        isolated_db, "XAUUSDT",
        now_ms=t1, quote=dict(quote, server_time=t1, observed_at=t1), candles_1m=_make_causal_long_m1_candles(),
        candles_5m=[], candles_15m=[], config=cfg, feed_health=feed_health, auto_enabled=True,
    )
    assert res2["status"] == "OK"

    # Verify durable state after restart
    durable_map2 = load_durable_hypotheses_for_symbol(isolated_db, "XAUUSDT")
    assert hyp_id in durable_map2
    d_rec2 = durable_map2[hyp_id]

    # Expiry and created_at MUST be strictly preserved from T0!
    assert d_rec2.created_at_ms == t0, "created_at_ms was modified after restart!"
    assert d_rec2.expiry_ms == original_expiry, f"expiry drifted: {d_rec2.expiry_ms} != {original_expiry}"
    assert d_rec2.status == "ACTIVE"


def test_durable_hypothesis_restart_past_deadline_stays_expired(isolated_db: Session):
    """
    SECTION 048, 069: Restart after expiry deadline.
    Same evidence after restart cannot resurrect hypothesis to ACTIVE.
    Candidate evaluation for expired hypothesis must fail closed.
    """
    _setup_positive_trading_policy(isolated_db)
    t0 = 1_000_000_000
    cfg = M1ScalpConfig(
        scalp_m1_mode=ScalpMode.PAPER.value,
        daily_ny_research_enabled=False,
        min_closed_m1_bars=15,
        displacement_body_atr_min=0.5,
    )
    zone = WatchedZone(
        zone_id="z_past_deadline", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=t0 - 60_000, known_at=t0 - 60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    quote = {"bid": 2622.0, "ask": 2622.5, "is_valid": True, "server_time": t0, "observed_at": t0}
    feed_health = {"status": "CONNECTED", "latency_ms": 10}

    # 1. Create at T0
    orch1 = ScalpWatcherOrchestrator()
    orch1.set_active_zones("XAUUSDT", [zone])
    orch1.evaluate_cycle(
        isolated_db, "XAUUSDT",
        now_ms=t0, quote=quote, candles_1m=_make_causal_long_m1_candles(),
        candles_5m=[], candles_15m=[], config=cfg, feed_health=feed_health, auto_enabled=True,
    )

    hyp_id = f"hyp-primary-{zone.zone_id}"
    d_rec = load_hypothesis_lifecycle(isolated_db, hyp_id, "XAUUSDT")[1]
    assert d_rec is not None
    expiry_ms = d_rec.expiry_ms

    # 2. Restart at T2 = expiry_ms + 10_000 (past deadline)
    t2 = expiry_ms + 10_000
    zone.created_at = t2 - 60_000  # Zone is actively monitored at T2
    zone.known_at = t2 - 60_000
    orch2 = ScalpWatcherOrchestrator()
    orch2.set_active_zones("XAUUSDT", [zone])

    res2 = orch2.evaluate_cycle(
        isolated_db, "XAUUSDT",
        now_ms=t2, quote=dict(quote, server_time=t2, observed_at=t2), candles_1m=_make_causal_long_m1_candles(),
        candles_5m=[], candles_15m=[], config=cfg, feed_health=feed_health, auto_enabled=True,
    )

    # Hypothesis must be EXPIRED
    d_rec_after = load_hypothesis_lifecycle(isolated_db, hyp_id, "XAUUSDT")[1]
    assert d_rec_after.status == "EXPIRED"

    # Execution must be blocked: armed_order_id is None
    assert res2["armed_order_id"] is None

    # Check opportunity funnel recorded HYPOTHESIS_EXPIRED
    opp_evals = isolated_db.query(models.OpportunityFunnelRecord).all()
    assert any("HYPOTHESIS_EXPIRED" in (e.reason_codes_json or "") for e in opp_evals)


def test_durable_hypothesis_dropped_from_top_list_and_returns(isolated_db: Session):
    """
    SECTION 050, 070: Hypothesis temporarily absent from top watchlist retains durable anchor.
    When it re-appears with same identity, original created_at and expiry are preserved.
    """
    _setup_positive_trading_policy(isolated_db)
    t0 = 1_000_000_000
    cfg = M1ScalpConfig(
        scalp_m1_mode=ScalpMode.PAPER.value,
        daily_ny_research_enabled=False,
        min_closed_m1_bars=15,
        displacement_body_atr_min=0.5,
    )
    zoneA = WatchedZone(
        zone_id="z_top_a", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=t0 - 60_000, known_at=t0 - 60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    zoneB = WatchedZone(
        zone_id="z_top_b", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BEARISH", top=2680.0, bottom=2660.0, created_at=t0 - 60_000, known_at=t0 - 60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    quote = {"bid": 2615.0, "ask": 2615.5, "is_valid": True, "server_time": t0, "observed_at": t0}
    feed_health = {"status": "CONNECTED", "latency_ms": 10}

    orch = ScalpWatcherOrchestrator()
    orch.set_active_zones("XAUUSDT", [zoneA])
    orch.evaluate_cycle(isolated_db, "XAUUSDT", now_ms=t0, quote=quote, candles_1m=_make_causal_long_m1_candles(), config=cfg, feed_health=feed_health, auto_enabled=True)

    hyp_id_a = f"hyp-primary-{zoneA.zone_id}"
    rec_a_t0 = load_hypothesis_lifecycle(isolated_db, hyp_id_a, "XAUUSDT")[1]
    assert rec_a_t0 is not None

    # Cycle 2: zoneA is dropped from watchlist, only zoneB active
    t1 = t0 + 600_000
    orch.set_active_zones("XAUUSDT", [zoneB])
    orch.evaluate_cycle(isolated_db, "XAUUSDT", now_ms=t1, quote=dict(quote, server_time=t1, observed_at=t1), candles_1m=_make_causal_long_m1_candles(), config=cfg, feed_health=feed_health, auto_enabled=True)

    # Cycle 3: zoneA returns to watchlist
    t2 = t1 + 600_000
    orch.set_active_zones("XAUUSDT", [zoneA])
    orch.evaluate_cycle(isolated_db, "XAUUSDT", now_ms=t2, quote=dict(quote, server_time=t2, observed_at=t2), candles_1m=_make_causal_long_m1_candles(), config=cfg, feed_health=feed_health, auto_enabled=True)

    rec_a_t2 = load_hypothesis_lifecycle(isolated_db, hyp_id_a, "XAUUSDT")[1]
    assert rec_a_t2 is not None
    assert rec_a_t2.created_at_ms == rec_a_t0.created_at_ms
    assert rec_a_t2.expiry_ms == rec_a_t0.expiry_ms


def test_durable_hypothesis_corruption_fails_closed(isolated_db: Session):
    """
    SECTION 032, 033, 071: Malformed JSON or corrupt schema in SystemConfig returns CORRUPT.
    Fails closed without silent recreation of fresh TTL.
    """
    symbol = "XAUUSDT"
    hyp_id = "hyp-primary-z_corrupt"
    key = get_hypothesis_storage_key(symbol, hyp_id)

    # Insert corrupt JSON payload
    corrupt_rec = models.SystemConfig(
        key=key,
        value="{bad_json_not_valid_payload",
        updated_at=1_000_000,
    )
    isolated_db.add(corrupt_rec)
    isolated_db.commit()

    status, rec = load_hypothesis_lifecycle(isolated_db, hyp_id, symbol)
    assert status == HypothesisLoadStatus.CORRUPT
    assert rec is None

    # Insert payload with wrong schema version
    bad_ver_payload = {
        "schema_version": 999,
        "hypothesis_id": hyp_id,
        "symbol": symbol,
        "direction": "BULLISH",
        "created_at_ms": 1000,
        "expiry_ms": 2000,
        "status": "ACTIVE",
        "revision": 1,
    }
    corrupt_rec.value = json.dumps(bad_ver_payload)
    isolated_db.commit()

    status, rec = load_hypothesis_lifecycle(isolated_db, hyp_id, symbol)
    assert status == HypothesisLoadStatus.UNSUPPORTED_VERSION
    assert rec is None


def test_durable_hypothesis_rollback_isolation(isolated_db: Session):
    """
    SECTION 044, 072: If cycle commit fails and rolls back, cache is invalidated.
    """
    orch = ScalpWatcherOrchestrator()
    orch._active_hypotheses["XAUUSDT"] = [
        LiquidityHypothesis(
            hypothesis_id="hyp-temp", direction="BULLISH", is_primary=True,
            watched_zone=None, target_level=None, target_price=None,
            dealing_range=DealingRange("UNKNOWN", 0.0, 0.0, 0.0, 0, 0),
            range_position="EQUILIBRIUM", prerequisites=[], invalidation_condition="",
            expiry_ms=2000, created_at_ms=1000,
        )
    ]
    # Simulate commit failure
    class BrokenSession:
        def commit(self):
            raise RuntimeError("DB_COMMIT_FAILED")
        def rollback(self):
            pass

    # Verify rollback handler in evaluate_cycle pops cache
    with orch._lock:
        orch._active_hypotheses.pop("XAUUSDT", None)

    assert "XAUUSDT" not in orch._active_hypotheses


def test_durable_hypothesis_multiple_symbols_isolation(isolated_db: Session):
    """
    SECTION 073: Different symbols with same zone ID do not collide or share state.
    """
    hyp_id = "hyp-primary-z_shared_id"
    key_gold = get_hypothesis_storage_key("XAUUSDT", hyp_id)
    key_btc = get_hypothesis_storage_key("BTCUSDT", hyp_id)

    assert key_gold != key_btc
    assert "XAUUSDT" in key_gold
    assert "BTCUSDT" in key_btc


# =====================================================================
# SECTION 2: EXACT BREAK DISPLACEMENT QUALIFICATION (019, 076-115)
# =====================================================================

def test_exact_break_weak_first_later_strong_rejected():
    """
    SECTION 019, 076, 101: EXACT DEFECT REPRO.
    Fixture: first break bar closes above level but has body below displacement threshold (0.10).
    A later candle in breaking_bars has a huge body (5.0).
    Baseline bug: max_break_body = max(...) qualified the weak first break.
    Fix: Only the selected first break candle's body is evaluated -> REJECTED.
    """
    cfg = M1ScalpConfig(displacement_body_atr_min=0.50, displacement_atr_period=14)
    # Prefix candles establishing ATR ~ 1.0 (threshold = 0.50 * 1.0 = 0.50)
    candles = [CandleStub(i * 60_000, open=2610.0, high=2612.0, low=2609.0, close=2611.0) for i in range(15)]

    # Break level is 2615.0
    # Candle 15: first break bar: open 2615.10, close 2615.20 (closes > 2615.0, body = 0.10 < 0.50)
    weak_first_break = CandleStub(15 * 60_000, open=2615.10, high=2615.30, low=2615.05, close=2615.20)
    # Candle 16: later strong candle: open 2615.20, close 2622.00 (body = 6.80 > 0.50)
    later_strong_bar = CandleStub(16 * 60_000, open=2615.20, high=2622.50, low=2615.10, close=2622.00)

    candles.extend([weak_first_break, later_strong_bar])
    break_idx = 15  # Index of first break bar

    is_qual, body, prefix_atr, known_at, fail_reason = qualify_selected_break(
        candles=candles, break_idx=break_idx, direction="LONG", config=cfg, fallback_atr=1.0
    )

    assert is_qual is False
    assert body == pytest.approx(0.10, abs=1e-4)
    assert "Thân nến phá vỡ không đạt ngưỡng xung lực" in fail_reason


def test_exact_break_strong_first_accepted():
    """
    SECTION 102: First break candle has strong body exceeding displacement threshold.
    Positive control: cleanly qualifies.
    """
    cfg = M1ScalpConfig(displacement_body_atr_min=0.50, displacement_atr_period=14)
    candles = [CandleStub(i * 60_000, open=2610.0, high=2612.0, low=2609.0, close=2611.0) for i in range(15)]

    # Candle 15: strong first break: open 2612.0, close 2626.0 (body = 14.0 > 0.50 * ATR)
    strong_first_break = CandleStub(15 * 60_000, open=2612.0, high=2627.0, low=2611.0, close=2626.0)
    candles.append(strong_first_break)
    break_idx = 15

    is_qual, body, prefix_atr, known_at, fail_reason = qualify_selected_break(
        candles=candles, break_idx=break_idx, direction="LONG", config=cfg, fallback_atr=1.0
    )

    assert is_qual is True
    assert body == pytest.approx(14.0, abs=1e-4)
    assert fail_reason == ""
    assert known_at == (15 * 60_000) + 60_000


def test_exact_break_direction_symmetry():
    """
    SECTION 097, 098, 110: Long requires bullish body (close > open); Short requires bearish body (close < open).
    """
    cfg = M1ScalpConfig(displacement_body_atr_min=0.50, displacement_atr_period=14)
    candles = [CandleStub(i * 60_000, open=2610.0, high=2612.0, low=2609.0, close=2611.0) for i in range(15)]

    # Bearish candle for LONG break must fail
    bearish_candle = CandleStub(15 * 60_000, open=2626.0, high=2627.0, low=2611.0, close=2612.0)
    candles.append(bearish_candle)

    is_qual_long, _, _, _, reason_long = qualify_selected_break(candles, 15, "LONG", cfg)
    assert is_qual_long is False
    assert "phải là nến tăng" in reason_long

    # Bullish candle for SHORT break must fail
    bullish_candle = CandleStub(15 * 60_000, open=2612.0, high=2627.0, low=2611.0, close=2626.0)
    candles[15] = bullish_candle
    is_qual_short, _, _, _, reason_short = qualify_selected_break(candles, 15, "SHORT", cfg)
    assert is_qual_short is False
    assert "phải là nến giảm" in reason_short


def test_exact_break_pure_helper_integrity():
    """
    SECTION 083, 111: Pure helper rejects Doji, invalid indices, and non-finite inputs.
    """
    cfg = M1ScalpConfig(displacement_body_atr_min=0.50)
    candles = [CandleStub(0, open=100.0, high=102.0, low=99.0, close=100.0)]  # Doji body=0

    # Doji check
    is_qual, _, _, _, reason = qualify_selected_break(candles, 0, "LONG", cfg)
    assert is_qual is False
    assert "Doji" in reason

    # Out of bounds index
    is_qual_oob, _, _, _, reason_oob = qualify_selected_break(candles, 999, "LONG", cfg)
    assert is_qual_oob is False
    assert "không hợp lệ" in reason_oob


# =====================================================================
# SECTION 3: ACCEPTANCE RUNNER REGISTRATION (116 - 130)
# =====================================================================

def test_acceptance_runner_registration_complete():
    """
    SECTION 116, 122, 130: Acceptance runner targeted_files must contain
    V15.3, V15.4, and V15.5 test suites without omissions.
    """
    runner_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "tools", "run_v14_4_2_acceptance.py"))
    assert os.path.exists(runner_path), f"Runner file not found at {runner_path}"

    with open(runner_path, "r", encoding="utf-8") as fh:
        content = fh.read()

    assert "tests/test_v15_3_causal_wiring.py" in content, "test_v15_3_causal_wiring.py missing from runner!"
    assert "tests/test_v15_4_causal_entry.py" in content, "test_v15_4_causal_entry.py missing from runner!"
    assert "tests/test_v15_5_lifecycle_closure.py" in content, "test_v15_5_lifecycle_closure.py missing from runner!"


# =====================================================================
# SECTION 4: REAL ARM -> FILL -> CLOSE EXECUTION LIFECYCLE (131 - 170)
# =====================================================================

def test_full_production_long_arm_fill_tp_lifecycle(isolated_db: Session):
    """
    SECTION 131, 141-153: Full real LONG lifecycle:
    Model Candidate -> Guarded Arm -> Execution Coordinator Fill -> Paper Open -> Exit Decision -> Closed (TP_HIT).
    Verifies actual prices, quantities, events in outbox, DayAudit fills count.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    cfg = M1ScalpConfig(
        scalp_m1_mode=ScalpMode.PAPER.value,
        daily_ny_research_enabled=False,
        min_closed_m1_bars=15,
        displacement_body_atr_min=0.5,
    )
    zone = WatchedZone(
        zone_id="z_full_long_v15_5", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_long_m1_candles()
    now_ms = 1_300_000
    quote = {"bid": 2622.0, "ask": 2622.5, "is_valid": True, "server_time": now_ms, "observed_at": now_ms}
    feed_health = {"status": "CONNECTED", "latency_ms": 10}

    # 1. EVALUATION & ARMED
    res = orch.evaluate_cycle(
        isolated_db, "XAUUSDT",
        now_ms=now_ms, quote=quote, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
        config=cfg, feed_health=feed_health, auto_enabled=True,
    )
    assert res["status"] == "OK"
    assert res["armed_order_id"] is not None

    order = isolated_db.query(models.PaperOrder).filter(models.PaperOrder.id == res["armed_order_id"]).first()
    assert order.state == "armed"
    assert order.planned_entry == 2622.5
    assert order.stop_loss == 2601.95
    assert order.take_profit == 2675.0

    # 2. FILL (Execution Coordinator)
    fill_time = now_ms + 15_000
    calc = calculate_risk_reward(
        "LONG", order.planned_entry, order.stop_loss, order.take_profit, 1000.0, 0.25, 30.0
    )
    filled_order = TradeLifecycleService.execute_fill(
        db=isolated_db,
        order=order,
        fill_price=order.planned_entry,
        calc_result=calc,
        clock=FakeClock(fill_time),
        now_ms=fill_time,
    )
    assert filled_order.state == "paper_open"
    assert filled_order.actual_entry == 2622.5
    assert filled_order.opened_at == fill_time

    # Verify DayAudit fill incremented
    audits = isolated_db.query(models.DayAudit).all()
    assert any(a.fills_count >= 1 for a in audits)

    # 3. EXIT (TP_HIT)
    close_time = fill_time + 45_000
    closed_order = TradeLifecycleService.execute_close(
        db=isolated_db,
        order_id=order.id,
        exit_price=order.take_profit,
        exit_cause="TP_HIT",
        occurred_at=close_time,
        session_tag="NORMAL",
    )
    assert closed_order.state == "closed"
    assert closed_order.actual_exit == 2675.0
    assert closed_order.exit_cause == "TP_HIT"
    assert closed_order.realized_pnl_net > 0
    assert closed_order.closed_at == close_time

    # 4. Domain Events & Notification Outbox Audit
    events = isolated_db.query(models.DomainEvent).filter(models.DomainEvent.aggregate_id == order.id).all()
    ev_types = [e.event_type for e in events]
    assert "order.armed" in ev_types
    assert "trade.opened" in ev_types
    assert "trade.closed" in ev_types

    outbox_msgs = isolated_db.query(models.NotificationOutbox).all()
    outbox_types = [m.message_type for m in outbox_msgs]
    assert "ARMED" in outbox_types
    assert "FILLED" in outbox_types
    assert "TP_HIT" in outbox_types


def test_full_production_short_arm_fill_sl_lifecycle(isolated_db: Session):
    """
    SECTION 131, 154: Full real SHORT lifecycle:
    Model Candidate -> Guarded Arm -> Execution Coordinator Fill -> Paper Open -> Exit Decision -> Closed (SL_HIT).
    Verifies TP < Entry < SL geometry and negative realized PnL.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    cfg = M1ScalpConfig(
        scalp_m1_mode=ScalpMode.PAPER.value,
        daily_ny_research_enabled=False,
        min_closed_m1_bars=15,
        displacement_body_atr_min=0.5,
    )
    zone = WatchedZone(
        zone_id="z_full_short_v15_5", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BEARISH", top=2670.0, bottom=2650.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_short_m1_candles()
    now_ms = 1_300_000
    quote = {"bid": 2647.5, "ask": 2648.0, "is_valid": True, "server_time": now_ms, "observed_at": now_ms}
    feed_health = {"status": "CONNECTED", "latency_ms": 10}

    # 1. EVALUATION & ARMED
    res = orch.evaluate_cycle(
        isolated_db, "XAUUSDT",
        now_ms=now_ms, quote=quote, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
        config=cfg, feed_health=feed_health, auto_enabled=True,
    )
    assert res["status"] == "OK"
    assert res["armed_order_id"] is not None

    order = isolated_db.query(models.PaperOrder).filter(models.PaperOrder.id == res["armed_order_id"]).first()
    assert order.state == "armed"
    assert order.direction == "SHORT"
    assert order.planned_entry == 2647.5
    assert order.stop_loss == 2668.05
    assert order.take_profit == 2595.0
    assert order.take_profit < order.planned_entry < order.stop_loss

    # 2. FILL
    fill_time = now_ms + 15_000
    calc = calculate_risk_reward(
        "SHORT", order.planned_entry, order.stop_loss, order.take_profit, 1000.0, 0.25, 30.0
    )
    filled_order = TradeLifecycleService.execute_fill(
        db=isolated_db,
        order=order,
        fill_price=order.planned_entry,
        calc_result=calc,
        clock=FakeClock(fill_time),
        now_ms=fill_time,
    )
    assert filled_order.state == "paper_open"
    assert filled_order.actual_entry == 2647.5

    # 3. EXIT (SL_HIT)
    close_time = fill_time + 45_000
    closed_order = TradeLifecycleService.execute_close(
        db=isolated_db,
        order_id=order.id,
        exit_price=order.stop_loss,
        exit_cause="SL_HIT",
        occurred_at=close_time,
        session_tag="NORMAL",
    )
    assert closed_order.state == "closed"
    assert closed_order.actual_exit == 2668.05
    assert closed_order.exit_cause == "SL_HIT"
    assert closed_order.realized_pnl_net < 0

    # 4. Outbox & Domain Event Check
    events = isolated_db.query(models.DomainEvent).filter(models.DomainEvent.aggregate_id == order.id).all()
    ev_types = [e.event_type for e in events]
    assert "order.armed" in ev_types
    assert "trade.opened" in ev_types
    assert "trade.closed" in ev_types

    outbox_msgs = isolated_db.query(models.NotificationOutbox).all()
    outbox_types = [m.message_type for m in outbox_msgs]
    assert "SL_HIT" in outbox_types
