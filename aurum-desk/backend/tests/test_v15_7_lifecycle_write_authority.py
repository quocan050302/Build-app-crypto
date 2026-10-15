"""
backend/tests/test_v15_7_lifecycle_write_authority.py
V15.7 - Lifecycle Write Authority, Entry Gating, Transaction Outcomes, and Cache Publication Test Suite.

Covers:
- Lifecycle stage failure/block strictly prevents order arming and WatchSetup/snapshot side effects.
- Clean rollback and status degradation on writer or commit failure.
- Active cache publication strictly restricted to entry-authorized, committed hypotheses.
- Positive controls for LONG and SHORT paper execution.
- Idempotency, guards regression, open position exit isolation, and API compatibility.
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import crud
import models
from services.clock import FakeClock
from services.liquidity_scenario_service import (
    DealingRange,
    DurableHypothesisRecord,
    HypothesisBatchLoadResult,
    HypothesisLoadIssue,
    HypothesisLoadStatus,
    HypothesisWriteOutcome,
    LifecycleCycleAuthority,
    LifecycleStageResult,
    LiquidityHypothesis,
    check_candidate_lifecycle_authority,
    compute_hypothesis_source_fingerprint,
    derive_cycle_status,
    get_hypothesis_storage_key,
    is_lifecycle_stage_success,
    load_durable_hypotheses_for_symbol,
    load_hypothesis_lifecycle,
    stage_hypothesis_lifecycle,
    stage_hypothesis_status_transition,
)
from services.scalp_m1_model_service import ScalpCandidate
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
from services.trade_lifecycle_service import TradeLifecycleService
from tests.test_v15_1_execution_lifecycle import (
    CandleStub,
    _make_causal_long_m1_candles,
    _make_causal_short_m1_candles,
    _setup_positive_trading_policy,
)


@pytest.fixture
def base_cfg():
    return M1ScalpConfig(
        scalp_m1_mode=ScalpMode.PAPER.value,
        daily_ny_research_enabled=False,
        min_closed_m1_bars=15,
        displacement_body_atr_min=0.5,
    )


# =====================================================================
# 1. REGRESSION TESTS (FAILED / BLOCKED LONG & SHORT)
# =====================================================================

def test_v15_7_regression_failed_long(isolated_db: Session, base_cfg: M1ScalpConfig):
    """
    SECTION 026, 027, 034, 035, 149:
    When stage_hypothesis_lifecycle returns FAILED for causal LONG candidate:
    - armed_order_id must be None.
    - No PaperOrder must be created in DB.
    - Cycle status must be DEGRADED (not OK).
    - lifecycle_write_status must be FAILED.
    - Active cache must NOT contain the failed hypothesis.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    zone = WatchedZone(
        zone_id="z_failed_long", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_long_m1_candles()
    now_ms = 1_300_000
    quote = {"bid": 2622.0, "ask": 2622.5, "is_valid": True}
    feed_health = {"status": "CONNECTED", "latency_ms": 10}

    with patch("services.liquidity_scenario_service.stage_hypothesis_lifecycle", return_value=LifecycleStageResult.FAILED):
        res = orch.evaluate_cycle(
            isolated_db, "XAUUSDT",
            now_ms=now_ms, quote=quote, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
            config=base_cfg, feed_health=feed_health, auto_enabled=True,
        )

    assert res["status"] == "DEGRADED"
    assert res["armed_order_id"] is None
    assert res["lifecycle_write_status"] == "FAILED"
    assert res["lifecycle_write_issues_count"] >= 1

    orders = isolated_db.query(models.PaperOrder).all()
    assert len(orders) == 0

    hlc_recs = isolated_db.query(models.SystemConfig).filter(models.SystemConfig.key.like("hlc:%")).all()
    assert len(hlc_recs) == 0

    active_hyps = orch.get_active_hypotheses("XAUUSDT")
    assert len(active_hyps) == 0


def test_v15_7_regression_blocked_long(isolated_db: Session, base_cfg: M1ScalpConfig):
    """
    SECTION 040, 063, 108, 150:
    When stage_hypothesis_lifecycle returns BLOCKED for causal LONG candidate:
    - Candidate must not be armed.
    - Funnel must record OpportunityReasonCode.HYPOTHESIS_WRITE_BLOCKED.
    - Cycle status must be PARTIAL (local block, session viable).
    - No PaperOrder or WatchSetup READY created for candidate.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    zone = WatchedZone(
        zone_id="z_blocked_long", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_long_m1_candles()
    now_ms = 1_300_000
    quote = {"bid": 2622.0, "ask": 2622.5, "is_valid": True}
    feed_health = {"status": "CONNECTED", "latency_ms": 10}

    with patch("services.liquidity_scenario_service.stage_hypothesis_lifecycle", return_value=LifecycleStageResult.BLOCKED):
        res = orch.evaluate_cycle(
            isolated_db, "XAUUSDT",
            now_ms=now_ms, quote=quote, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
            config=base_cfg, feed_health=feed_health, auto_enabled=True,
        )

    assert res["status"] == "PARTIAL"
    assert res["armed_order_id"] is None
    assert res["lifecycle_write_status"] == "BLOCKED"

    orders = isolated_db.query(models.PaperOrder).all()
    assert len(orders) == 0

    opps = isolated_db.query(models.OpportunityFunnelRecord).all()
    reasons = [r for o in opps if o.reason_codes_json for r in json.loads(o.reason_codes_json)]
    assert OpportunityReasonCode.HYPOTHESIS_WRITE_BLOCKED.value in reasons


def test_v15_7_regression_failed_short(isolated_db: Session, base_cfg: M1ScalpConfig):
    """
    SECTION 151:
    When stage_hypothesis_lifecycle returns FAILED for causal SHORT candidate:
    - Same failure-closed behavior applies symmetrically to SHORT as to LONG.
    - No order armed, cycle status DEGRADED.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    zone = WatchedZone(
        zone_id="z_failed_short", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BEARISH", top=2670.0, bottom=2650.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_short_m1_candles()
    now_ms = 1_300_000
    quote = {"bid": 2647.0, "ask": 2647.5, "is_valid": True}
    feed_health = {"status": "CONNECTED", "latency_ms": 10}

    with patch("services.liquidity_scenario_service.stage_hypothesis_lifecycle", return_value=LifecycleStageResult.FAILED):
        res = orch.evaluate_cycle(
            isolated_db, "XAUUSDT",
            now_ms=now_ms, quote=quote, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
            config=base_cfg, feed_health=feed_health, auto_enabled=True,
        )

    assert res["status"] == "DEGRADED"
    assert res["armed_order_id"] is None
    assert isolated_db.query(models.PaperOrder).count() == 0


def test_v15_7_regression_blocked_short(isolated_db: Session, base_cfg: M1ScalpConfig):
    """
    SECTION 152:
    When stage_hypothesis_lifecycle returns BLOCKED for causal SHORT candidate:
    - Candidate blocked, no order created, direction and SL/TP remain untouched.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    zone = WatchedZone(
        zone_id="z_blocked_short", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BEARISH", top=2670.0, bottom=2650.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_short_m1_candles()
    now_ms = 1_300_000
    quote = {"bid": 2647.0, "ask": 2647.5, "is_valid": True}
    feed_health = {"status": "CONNECTED", "latency_ms": 10}

    with patch("services.liquidity_scenario_service.stage_hypothesis_lifecycle", return_value=LifecycleStageResult.BLOCKED):
        res = orch.evaluate_cycle(
            isolated_db, "XAUUSDT",
            now_ms=now_ms, quote=quote, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
            config=base_cfg, feed_health=feed_health, auto_enabled=True,
        )

    assert res["status"] == "PARTIAL"
    assert res["armed_order_id"] is None
    assert isolated_db.query(models.PaperOrder).count() == 0


# =====================================================================
# 2. POSITIVE CONTROLS (LONG & SHORT REAL WRITER)
# =====================================================================

def test_v15_7_positive_control_long(isolated_db: Session, base_cfg: M1ScalpConfig):
    """
    SECTION 153:
    Positive control LONG: real writer, no failure injection.
    - LONG order armed successfully via production flow.
    - HLC durable lifecycle record exists in DB after commit.
    - Cache contains active hypothesis.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    zone = WatchedZone(
        zone_id="z_pos_long", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_long_m1_candles()
    now_ms = 1_300_000
    quote = {"bid": 2622.0, "ask": 2622.5, "is_valid": True}
    feed_health = {"status": "CONNECTED", "latency_ms": 10}

    res = orch.evaluate_cycle(
        isolated_db, "XAUUSDT",
        now_ms=now_ms, quote=quote, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
        config=base_cfg, feed_health=feed_health, auto_enabled=True,
    )

    assert res["status"] == "OK"
    assert res["armed_order_id"] is not None
    assert res["lifecycle_write_status"] == "OK"
    assert res["transaction_status"] == "COMMITTED"

    order = isolated_db.query(models.PaperOrder).filter(models.PaperOrder.id == res["armed_order_id"]).first()
    assert order is not None
    assert order.direction == "LONG"
    assert order.stop_loss < order.planned_entry < order.take_profit
    assert order.quantity > 0

    # HLC record exists in DB
    hlc_recs = isolated_db.query(models.SystemConfig).filter(models.SystemConfig.key.like("hlc:%")).all()
    assert len(hlc_recs) >= 1

    # Active hypotheses in cache
    active_hyps = orch.get_active_hypotheses("XAUUSDT")
    assert len(active_hyps) >= 1


def test_v15_7_positive_control_short(isolated_db: Session, base_cfg: M1ScalpConfig):
    """
    SECTION 154:
    Positive control SHORT: real writer, causal SHORT fixture.
    - SHORT order armed with TP < planned_entry < SL and finite quantity.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    zone = WatchedZone(
        zone_id="z_pos_short", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BEARISH", top=2670.0, bottom=2650.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_short_m1_candles()
    now_ms = 1_300_000
    quote = {"bid": 2647.0, "ask": 2647.5, "is_valid": True}
    feed_health = {"status": "CONNECTED", "latency_ms": 10}

    res = orch.evaluate_cycle(
        isolated_db, "XAUUSDT",
        now_ms=now_ms, quote=quote, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
        config=base_cfg, feed_health=feed_health, auto_enabled=True,
    )

    assert res["status"] == "OK"
    assert res["armed_order_id"] is not None

    order = isolated_db.query(models.PaperOrder).filter(models.PaperOrder.id == res["armed_order_id"]).first()
    assert order is not None
    assert order.direction == "SHORT"
    assert order.take_profit < order.planned_entry < order.stop_loss
    assert order.quantity > 0


# =====================================================================
# 3. CLASSIFICATION & HELPER PURITY
# =====================================================================

@pytest.mark.parametrize("res_val,expected", [
    (LifecycleStageResult.CREATED, True),
    (LifecycleStageResult.UPDATED, True),
    (LifecycleStageResult.NO_CHANGE, True),
    (LifecycleStageResult.STAGED, True),
    (LifecycleStageResult.BLOCKED, False),
    (LifecycleStageResult.FAILED, False),
    (None, False),
    ("UNKNOWN_RESULT", False),
    (123, False),
])
def test_v15_7_success_enums_classification(res_val, expected):
    """
    SECTION 036, 049, 050, 155:
    is_lifecycle_stage_success returns True ONLY for CREATED, UPDATED, NO_CHANGE, STAGED.
    """
    assert is_lifecycle_stage_success(res_val) is expected


def test_v15_7_unknown_result_fails_closed(isolated_db: Session, base_cfg: M1ScalpConfig):
    """
    SECTION 042, 156:
    Unrecognized result or None from writer is treated as write failure / non-authorized.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    zone = WatchedZone(
        zone_id="z_unknown_res", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_long_m1_candles()
    now_ms = 1_300_000
    quote = {"bid": 2622.0, "ask": 2622.5, "is_valid": True}
    feed_health = {"status": "CONNECTED", "latency_ms": 10}

    with patch("services.liquidity_scenario_service.stage_hypothesis_lifecycle", return_value="BOGUS_RESULT"):
        res = orch.evaluate_cycle(
            isolated_db, "XAUUSDT",
            now_ms=now_ms, quote=quote, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
            config=base_cfg, feed_health=feed_health, auto_enabled=True,
        )

    assert res["status"] != "OK"
    assert res["armed_order_id"] is None
    assert isolated_db.query(models.PaperOrder).count() == 0


# =====================================================================
# 4. EXCEPTION & TRANSACTION OUTCOMES
# =====================================================================

def test_v15_7_writer_raises_exception_safe(isolated_db: Session, base_cfg: M1ScalpConfig):
    """
    SECTION 068, 157:
    Exception raised during stage_hypothesis_lifecycle is safely caught,
    marks write failure, and prevents new order creation.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    zone = WatchedZone(
        zone_id="z_writer_exc", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_long_m1_candles()
    now_ms = 1_300_000
    quote = {"bid": 2622.0, "ask": 2622.5, "is_valid": True}
    feed_health = {"status": "CONNECTED", "latency_ms": 10}

    with patch("services.liquidity_scenario_service.stage_hypothesis_lifecycle", side_effect=RuntimeError("DB disk full")):
        res = orch.evaluate_cycle(
            isolated_db, "XAUUSDT",
            now_ms=now_ms, quote=quote, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
            config=base_cfg, feed_health=feed_health, auto_enabled=True,
        )

    assert res["status"] == "DEGRADED"
    assert res["armed_order_id"] is None
    assert isolated_db.query(models.PaperOrder).count() == 0


def test_v15_7_query_failure_inside_writer(isolated_db: Session, base_cfg: M1ScalpConfig):
    """
    SECTION 158:
    Inject failure at query point inside stage_hypothesis_lifecycle.
    Writer returns FAILED and caller handles it cleanly.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    zone = WatchedZone(
        zone_id="z_query_fail", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_long_m1_candles()
    now_ms = 1_300_000
    quote = {"bid": 2622.0, "ask": 2622.5, "is_valid": True}
    feed_health = {"status": "CONNECTED", "latency_ms": 10}

    real_query = isolated_db.query

    def buggy_query(model, *args, **kwargs):
        q = real_query(model, *args, **kwargs)
        if model is models.SystemConfig:
            real_filter = q.filter
            def buggy_filter(*f_args, **f_kwargs):
                fq = real_filter(*f_args, **f_kwargs)
                for arg in f_args:
                    val = getattr(getattr(arg, 'right', None), 'value', None)
                    if isinstance(val, str) and val.startswith('hlc:'):
                        raise RuntimeError("Injected SystemConfig query failure inside writer")
                return fq
            q.filter = buggy_filter
        return q

    with patch.object(isolated_db, "query", side_effect=buggy_query):
        res = orch.evaluate_cycle(
            isolated_db, "XAUUSDT",
            now_ms=now_ms, quote=quote, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
            config=base_cfg, feed_health=feed_health, auto_enabled=True,
        )

    assert res["status"] == "DEGRADED"
    assert res["armed_order_id"] is None


def test_v15_7_commit_failure_atomicity(isolated_db: Session, base_cfg: M1ScalpConfig):
    """
    SECTION 123, 124, 160:
    When db.commit() raises an exception during evaluate_cycle:
    - armed_order_id must be None.
    - transaction_status must be FAILED.
    - status must be DEGRADED.
    - Cache must be cleared (no active hypotheses).
    """
    _setup_positive_trading_policy(isolated_db)
    crud.get_or_create_today_audit(isolated_db)
    orch = ScalpWatcherOrchestrator()
    zone = WatchedZone(
        zone_id="z_commit_fail", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_long_m1_candles()
    now_ms = 1_300_000
    quote = {"bid": 2622.0, "ask": 2622.5, "is_valid": True}
    feed_health = {"status": "CONNECTED", "latency_ms": 10}

    with patch.object(isolated_db, "commit", side_effect=RuntimeError("Injected Commit Error")):
        res = orch.evaluate_cycle(
            isolated_db, "XAUUSDT",
            now_ms=now_ms, quote=quote, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
            config=base_cfg, feed_health=feed_health, auto_enabled=True,
        )

    assert res["armed_order_id"] is None
    assert res["status"] == "DEGRADED"
    assert res["transaction_status"] == "FAILED"
    assert len(orch.get_active_hypotheses("XAUUSDT")) == 0


def test_v15_7_rollback_clean_subsequent_session(isolated_db: Session, base_cfg: M1ScalpConfig):
    """
    SECTION 161:
    After a cycle with rollback, a subsequent cycle with a healthy session succeeds cleanly.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    zone = WatchedZone(
        zone_id="z_subseq_test", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_long_m1_candles()
    now_ms = 1_300_000
    quote = {"bid": 2622.0, "ask": 2622.5, "is_valid": True}
    feed_health = {"status": "CONNECTED", "latency_ms": 10}

    # Cycle 1: failed
    with patch("services.liquidity_scenario_service.stage_hypothesis_lifecycle", return_value=LifecycleStageResult.FAILED):
        res1 = orch.evaluate_cycle(
            isolated_db, "XAUUSDT",
            now_ms=now_ms, quote=quote, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
            config=base_cfg, feed_health=feed_health, auto_enabled=True,
        )
    assert res1["status"] == "DEGRADED"

    # Cycle 2: healthy session
    res2 = orch.evaluate_cycle(
        isolated_db, "XAUUSDT",
        now_ms=now_ms + 60_000, quote=quote, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
        config=base_cfg, feed_health=feed_health, auto_enabled=True,
    )
    assert res2["status"] == "OK"
    assert res2["armed_order_id"] is not None


# =====================================================================
# 5. SIDE EFFECT ISOLATION (WATCHSETUP, SNAPSHOT, ORDER, EVENTS)
# =====================================================================

def test_v15_7_watchsetup_no_side_effects(isolated_db: Session, base_cfg: M1ScalpConfig):
    """
    SECTION 098, 162:
    When candidate is blocked by lifecycle authority, no new READY WatchSetup is created.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    zone = WatchedZone(
        zone_id="z_ws_side_eff", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_long_m1_candles()
    now_ms = 1_300_000
    quote = {"bid": 2622.0, "ask": 2622.5, "is_valid": True}
    feed_health = {"status": "CONNECTED", "latency_ms": 10}

    initial_ws_count = isolated_db.query(models.WatchSetup).count()

    with patch("services.liquidity_scenario_service.stage_hypothesis_lifecycle", return_value=LifecycleStageResult.FAILED):
        orch.evaluate_cycle(
            isolated_db, "XAUUSDT",
            now_ms=now_ms, quote=quote, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
            config=base_cfg, feed_health=feed_health, auto_enabled=True,
        )

    final_ws_count = isolated_db.query(models.WatchSetup).count()
    assert final_ws_count == initial_ws_count


def test_v15_7_scenario_snapshot_no_side_effects(isolated_db: Session, base_cfg: M1ScalpConfig):
    """
    SECTION 099, 163:
    When candidate is blocked early by lifecycle gate, save_immutable_scenario_snapshot is not called.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    zone = WatchedZone(
        zone_id="z_snap_side_eff", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_long_m1_candles()
    now_ms = 1_300_000
    quote = {"bid": 2622.0, "ask": 2622.5, "is_valid": True}
    feed_health = {"status": "CONNECTED", "latency_ms": 10}

    initial_snaps = isolated_db.query(models.SystemConfig).filter(models.SystemConfig.key.like("snap:%")).count()

    with patch("services.liquidity_scenario_service.stage_hypothesis_lifecycle", return_value=LifecycleStageResult.BLOCKED):
        orch.evaluate_cycle(
            isolated_db, "XAUUSDT",
            now_ms=now_ms, quote=quote, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
            config=base_cfg, feed_health=feed_health, auto_enabled=True,
        )

    final_snaps = isolated_db.query(models.SystemConfig).filter(models.SystemConfig.key.like("snap:%")).count()
    assert final_snaps == initial_snaps


def test_v15_7_order_no_side_effects_on_existing_orders(isolated_db: Session, base_cfg: M1ScalpConfig):
    """
    SECTION 096, 164, 177:
    Existing orders in DB remain completely untouched when subsequent cycles experience writer failures.
    """
    _setup_positive_trading_policy(isolated_db)
    existing_order = models.PaperOrder(
        id="ord_existing_safe",
        instrument="XAUUSDT",
        direction="LONG",
        state="armed",
        order_type="LIMIT",
        planned_entry=2600.0,
        stop_loss=2590.0,
        take_profit=2620.0,
        quantity=0.25,
        initial_risk_usdt=2.5,
        created_at=500_000,
    )
    isolated_db.add(existing_order)
    isolated_db.commit()

    orch = ScalpWatcherOrchestrator()
    zone = WatchedZone(
        zone_id="z_fail_test", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_long_m1_candles()
    now_ms = 1_300_000
    quote = {"bid": 2622.0, "ask": 2622.5, "is_valid": True}

    with patch("services.liquidity_scenario_service.stage_hypothesis_lifecycle", return_value=LifecycleStageResult.FAILED):
        orch.evaluate_cycle(
            isolated_db, "XAUUSDT",
            now_ms=now_ms, quote=quote, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
            config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
        )

    reloaded = isolated_db.query(models.PaperOrder).filter(models.PaperOrder.id == "ord_existing_safe").first()
    assert reloaded is not None
    assert reloaded.planned_entry == 2600.0
    assert reloaded.stop_loss == 2590.0
    assert reloaded.take_profit == 2620.0
    assert reloaded.quantity == 0.25
    assert reloaded.state == "armed"


# =====================================================================
# 6. SHADOW MODE & AUTO-DISABLED TESTS
# =====================================================================

def test_v15_7_shadow_mode_writer_failed(isolated_db: Session):
    """
    SECTION 166:
    In SHADOW mode, writer FAILED does not create orders and marks cycle DEGRADED.
    """
    _setup_positive_trading_policy(isolated_db)
    cfg = M1ScalpConfig(scalp_m1_mode=ScalpMode.SHADOW.value, min_closed_m1_bars=15)
    orch = ScalpWatcherOrchestrator()
    zone = WatchedZone(
        zone_id="z_shadow_fail", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_long_m1_candles()
    now_ms = 1_300_000
    quote = {"bid": 2622.0, "ask": 2622.5, "is_valid": True}

    with patch("services.liquidity_scenario_service.stage_hypothesis_lifecycle", return_value=LifecycleStageResult.FAILED):
        res = orch.evaluate_cycle(
            isolated_db, "XAUUSDT",
            now_ms=now_ms, quote=quote, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
            config=cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
        )

    assert res["status"] == "DEGRADED"
    assert res["armed_order_id"] is None
    assert isolated_db.query(models.PaperOrder).count() == 0


def test_v15_7_auto_disabled_guard(isolated_db: Session, base_cfg: M1ScalpConfig):
    """
    SECTION 103, 169:
    Lifecycle staging succeeds, but global auto is False -> arming refused due to GLOBAL_AUTO_DISABLED.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    zone = WatchedZone(
        zone_id="z_auto_off", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_long_m1_candles()
    now_ms = 1_300_000
    quote = {"bid": 2622.0, "ask": 2622.5, "is_valid": True}

    res = orch.evaluate_cycle(
        isolated_db, "XAUUSDT",
        now_ms=now_ms, quote=quote, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
        config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=False,
    )

    # In SHADOW downgrade when auto_enabled is False
    assert res["armed_order_id"] is None
    assert isolated_db.query(models.PaperOrder).count() == 0


# =====================================================================
# 7. MULTI-ZONE ISOLATION (ONE BLOCKED, ONE VALID vs ONE FAILED)
# =====================================================================

def test_v15_7_two_zones_one_blocked_one_valid(isolated_db: Session, base_cfg: M1ScalpConfig):
    """
    SECTION 071, 170:
    Two zones in healthy session: zone A writer BLOCKED, zone B valid.
    Zone A is blocked; zone B evaluates and arms normally.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()

    zone_a = WatchedZone(
        zone_id="z_blocked_a", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BEARISH", top=2670.0, bottom=2650.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    zone_b = WatchedZone(
        zone_id="z_valid_b", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone_a, zone_b])

    candles_1m = _make_causal_long_m1_candles()
    now_ms = 1_300_000
    quote = {"bid": 2622.0, "ask": 2622.5, "is_valid": True}

    real_stage = stage_hypothesis_lifecycle

    def mock_stage(db, sym, hyp, now_ms=None):
        if hyp.watched_zone and hyp.watched_zone.zone_id == "z_blocked_a":
            return LifecycleStageResult.BLOCKED
        return real_stage(db, sym, hyp, now_ms=now_ms)

    with patch("services.liquidity_scenario_service.stage_hypothesis_lifecycle", side_effect=mock_stage):
        res = orch.evaluate_cycle(
            isolated_db, "XAUUSDT",
            now_ms=now_ms, quote=quote, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
            config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
        )

    assert res["status"] == "PARTIAL"
    assert res["armed_order_id"] is not None
    order = isolated_db.query(models.PaperOrder).filter(models.PaperOrder.id == res["armed_order_id"]).first()
    assert order is not None
    assert "z_valid_b" in order.id


def test_v15_7_two_zones_one_failed(isolated_db: Session, base_cfg: M1ScalpConfig):
    """
    SECTION 064, 072, 082, 171:
    When any zone writer returns FAILED, persistence integrity is compromised:
    the entire cycle halts new entries to protect transaction integrity.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()

    zone_a = WatchedZone(
        zone_id="z_failed_a", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    zone_b = WatchedZone(
        zone_id="z_valid_b", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone_a, zone_b])

    candles_1m = _make_causal_long_m1_candles()
    now_ms = 1_300_000
    quote = {"bid": 2622.0, "ask": 2622.5, "is_valid": True}

    real_stage = stage_hypothesis_lifecycle

    def mock_stage(db, sym, hyp, now_ms=None):
        if hyp.watched_zone and hyp.watched_zone.zone_id == "z_failed_a":
            return LifecycleStageResult.FAILED
        return real_stage(db, sym, hyp, now_ms=now_ms)

    with patch("services.liquidity_scenario_service.stage_hypothesis_lifecycle", side_effect=mock_stage):
        res = orch.evaluate_cycle(
            isolated_db, "XAUUSDT",
            now_ms=now_ms, quote=quote, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
            config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
        )

    assert res["status"] == "DEGRADED"
    assert res["armed_order_id"] is None
    assert isolated_db.query(models.PaperOrder).count() == 0


# =====================================================================
# 8. DIRECT _ARM_PAPER_CANDIDATE BOUNDARY DEFENSE
# =====================================================================

def test_v15_7_arm_direct_call_missing_or_rejected_authority(isolated_db: Session, base_cfg: M1ScalpConfig):
    """
    SECTION 090, 174:
    Direct invocation of _arm_paper_candidate for reversal candidate without valid authority:
    - authority_context is None -> returns None (no bypass via context=None).
    - authority_context with blocked/failed status -> returns None.
    - No WatchSetup READY or PaperOrder created.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    calc = type("CalcStub", (), {"is_valid": True, "can_execute": True, "quantity": 0.1})()

    cand = ScalpCandidate(
        is_eligible=True,
        model_id="SMC_M1_REVERSAL",
        direction="LONG",
        candidate_key="cand_direct_no_auth",
        symbol="XAUUSDT",
        planned_entry=2620.0,
        stop_loss=2610.0,
        take_profit=2650.0,
        calculation=calc,
        zone_id="z_direct_test",
    )

    # 1. authority_context is None
    order_none = orch._arm_paper_candidate(
        isolated_db, cand, now_ms=1_000_000, clock=FakeClock(1_000_000),
        quote={"bid": 2620.0, "ask": 2620.5, "is_valid": True},
        policy_settings={"capital_usdt": 1000.0, "risk_pct": 0.25, "leverage": 30, "margin_mode": "ISOLATED", "max_daily_trades": 3},
        authority_context=None,
    )
    assert order_none is None
    assert isolated_db.query(models.PaperOrder).count() == 0

    # 2. authority_context with blocked outcome
    bad_auth = LifecycleCycleAuthority()
    bad_auth.zone_outcomes["z_direct_test"] = HypothesisWriteOutcome(
        hypothesis_id="hyp_direct_test",
        storage_key="hlc:XAUUSDT:123",
        zone_id="z_direct_test",
        operation="UPSERT_HYPOTHESIS",
        result=LifecycleStageResult.BLOCKED,
        is_success=False,
        reason_code=OpportunityReasonCode.HYPOTHESIS_WRITE_BLOCKED.value,
    )

    order_blocked = orch._arm_paper_candidate(
        isolated_db, cand, now_ms=1_000_000, clock=FakeClock(1_000_000),
        quote={"bid": 2620.0, "ask": 2620.5, "is_valid": True},
        policy_settings={"capital_usdt": 1000.0, "risk_pct": 0.25, "leverage": 30, "margin_mode": "ISOLATED", "max_daily_trades": 3},
        authority_context=bad_auth,
    )
    assert order_blocked is None
    assert isolated_db.query(models.PaperOrder).count() == 0


def test_v15_7_arm_direct_call_valid_authority(isolated_db: Session, base_cfg: M1ScalpConfig):
    """
    SECTION 175:
    Direct invocation of _arm_paper_candidate with valid authority context passes through guards and arms order.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    calc = type("CalcStub", (), {
        "is_valid": True,
        "can_execute": True,
        "meets_min_rr": True,
        "quantity": 0.1,
        "gross_rr": 3.0,
        "net_rr": 2.8,
        "net_reward_usdt": 50.0,
        "net_risk_usdt": 2.5,
        "leverage": 30.0,
        "margin_mode": "ISOLATED",
        "effective_risk_pct": 0.25,
        "estimated_liquidation": 2550.0,
        "est_fill_fee_usdt": 0.1,
        "est_close_fee_usdt": 0.1,
        "est_slippage_usdt": 0.1,
        "total_friction_usdt": 0.3,
        "cash_buffer_usdt": 10.0,
        "margin_used_usdt": 10.0,
    })()

    cand = ScalpCandidate(
        is_eligible=True,
        model_id="SMC_M1_REVERSAL",
        direction="LONG",
        candidate_key="cand_direct_valid_auth",
        symbol="XAUUSDT",
        planned_entry=2620.0,
        stop_loss=2610.0,
        take_profit=2650.0,
        net_rr=2.8,
        gross_rr=3.0,
        calculation=calc,
        zone_id="z_direct_valid",
    )

    good_auth = LifecycleCycleAuthority()
    good_auth.zone_outcomes["z_direct_valid"] = HypothesisWriteOutcome(
        hypothesis_id="hyp_direct_valid",
        storage_key="hlc:XAUUSDT:456",
        zone_id="z_direct_valid",
        operation="UPSERT_HYPOTHESIS",
        result=LifecycleStageResult.CREATED,
        is_success=True,
    )

    quote = {"bid": 2620.0, "ask": 2620.5, "last": 2620.25, "is_valid": True}

    order = orch._arm_paper_candidate(
        isolated_db, cand, now_ms=1_000_000, clock=FakeClock(1_000_000),
        quote=quote,
        policy_settings={"capital_usdt": 1000.0, "risk_pct": 0.25, "leverage": 30, "margin_mode": "ISOLATED", "max_daily_trades": 3},
        authority_context=good_auth,
        auto_enabled=True,
        feed_health={"status": "CONNECTED", "latency_ms": 10},
    )
    assert order is not None
    assert order.state == "armed"


# =====================================================================
# 9. EXPIRY & ANCHOR INTEGRITY TESTS
# =====================================================================

def test_v15_7_expired_deadline_blocks_entry_even_if_unwritten(isolated_db: Session, base_cfg: M1ScalpConfig):
    """
    SECTION 117, 180:
    Durable record is past its expiry deadline (now_ms >= expiry_ms).
    Even if stage_hypothesis_status_transition fails to write EXPIRED to DB,
    the deadline strictly blocks entry.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()

    # Pre-seed an overdue record in DB
    now_ms = 1_300_000
    hyp_id = "hyp_overdue_test"
    key = get_hypothesis_storage_key("XAUUSDT", hyp_id)
    fp = compute_hypothesis_source_fingerprint(hyp_id, "XAUUSDT", "BULLISH", "z_overdue_zone", 60_000)
    payload = {
        "schema_version": 1,
        "hypothesis_id": hyp_id,
        "symbol": "XAUUSDT",
        "direction": "BULLISH",
        "is_primary": True,
        "zone_id": "z_overdue_zone",
        "created_at_ms": 60_000,
        "expiry_ms": now_ms - 10_000,  # Overdue by 10s
        "status": "ACTIVE",
        "revision": 1,
        "source_fingerprint": fp,
        "updated_at_ms": 60_000,
    }
    isolated_db.add(models.SystemConfig(key=key, value=json.dumps(payload), updated_at=60_000))
    isolated_db.commit()

    zone = WatchedZone(
        zone_id="z_overdue_zone", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_long_m1_candles()
    quote = {"bid": 2622.0, "ask": 2622.5, "is_valid": True}

    # Simulate transition failure
    with patch("services.liquidity_scenario_service.stage_hypothesis_status_transition", return_value=LifecycleStageResult.FAILED):
        res = orch.evaluate_cycle(
            isolated_db, "XAUUSDT",
            now_ms=now_ms, quote=quote, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
            config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
        )

    assert res["armed_order_id"] is None
    assert isolated_db.query(models.PaperOrder).count() == 0


def test_v15_7_recovery_preserves_expiry_and_created_anchors(isolated_db: Session, base_cfg: M1ScalpConfig):
    """
    SECTION 055, 188:
    Durable record created_at_ms and expiry_ms anchors are strictly preserved
    across failed cycles and subsequent recovery.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    zone = WatchedZone(
        zone_id="z_anchor_pres", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_long_m1_candles()
    now_ms = 1_300_000
    quote = {"bid": 2622.0, "ask": 2622.5, "is_valid": True}

    # 1. Normal first run creates durable record
    res1 = orch.evaluate_cycle(
        isolated_db, "XAUUSDT",
        now_ms=now_ms, quote=quote, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
        config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
    )
    assert res1["status"] == "OK"

    hlc_row = isolated_db.query(models.SystemConfig).filter(models.SystemConfig.key.like("hlc:%")).first()
    assert hlc_row is not None
    orig_data = json.loads(hlc_row.value)
    orig_c_at = orig_data["created_at_ms"]
    orig_e_at = orig_data["expiry_ms"]

    # 2. Subsequent cycle with failed write
    with patch("services.liquidity_scenario_service.stage_hypothesis_lifecycle", return_value=LifecycleStageResult.FAILED):
        orch.evaluate_cycle(
            isolated_db, "XAUUSDT",
            now_ms=now_ms + 60_000, quote=quote, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
            config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
        )

    # 3. Third cycle with normal write
    res3 = orch.evaluate_cycle(
        isolated_db, "XAUUSDT",
        now_ms=now_ms + 120_000, quote=quote, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
        config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
    )
    assert res3["status"] == "OK"

    isolated_db.refresh(hlc_row)
    reloaded_data = json.loads(hlc_row.value)
    assert reloaded_data["created_at_ms"] == orig_c_at
    assert reloaded_data["expiry_ms"] == orig_e_at


# =====================================================================
# 10. STATUS MATRIX & API DIAGNOSTICS
# =====================================================================

@pytest.mark.parametrize("read_st,write_st,commit_ok,fail_w,block_w,expected", [
    ("COMPLETE", "OK", True, False, False, "OK"),
    ("COMPLETE", "BLOCKED", True, False, True, "PARTIAL"),
    ("COMPLETE", "FAILED", True, True, False, "DEGRADED"),
    ("COMPLETE", "OK", False, False, False, "DEGRADED"),  # Commit failed -> DEGRADED
    ("PARTIAL", "OK", True, False, False, "PARTIAL"),
    ("READ_FAILED", "OK", True, False, False, "DEGRADED"),
    ("READ_FAILED", "FAILED", False, True, False, "DEGRADED"),
    ("COMPLETE", "BLOCKED", False, False, True, "DEGRADED"),  # Commit failure overrides blocked
])
def test_v15_7_status_matrix(read_st, write_st, commit_ok, fail_w, block_w, expected):
    """
    SECTION 126 - 130, 191:
    Deterministic resolution of derive_cycle_status across all combinations.
    """
    status = derive_cycle_status(
        read_status=read_st,
        write_status=write_st,
        commit_ok=commit_ok,
        has_failed_write=fail_w,
        has_blocked_write=block_w,
    )
    assert status == expected


def test_v15_7_acceptance_runner_registration_assertion():
    """
    SECTION 194:
    Assert that test_v15_7_lifecycle_write_authority.py is registered in tools/run_v14_4_2_acceptance.py.
    """
    acceptance_path = os.path.join(os.path.dirname(__file__), "..", "..", "tools", "run_v14_4_2_acceptance.py")
    assert os.path.isfile(acceptance_path)
    with open(acceptance_path, "r", encoding="utf-8") as f:
        content = f.read()
    assert "test_v15_7_lifecycle_write_authority.py" in content
