"""
backend/tests/test_v15_8_database_failure_recovery.py
V15.8 - Database Failure Recovery and Transaction Invalidation Test Suite.

Covers:
- Real IntegrityError during flush in stage_hypothesis_lifecycle() does NOT propagate PendingRollbackError.
- evaluate_cycle() safely executes db.rollback() and finalizes cycle as DEGRADED.
- No subsequent queries on poisoned session (risk_settings, audit, funnel).
- _arm_paper_candidate() is NOT called when transaction fails.
- WatchSetup, snapshot, and PaperOrder changes are fully rolled back.
- Expire path (stage_hypothesis_status_transition) real IntegrityError recovers safely.
- Writer returning FAILED aborts cycle and triggers rollback without false COMMITTED status.
- BLOCKED writer outcome remains isolated to the affected zone without rolling back healthy sessions.
- db.commit() failure rolls back and degrades gracefully.
- db.rollback() failure reports ROLLBACK_FAILED honestly without crashing.
- Cache is cleared upon persistence failure; API does NOT falsely report HEALTHY.
- Next evaluation cycle with clean session recovers and successfully arms valid setups.
- Existing committed orders and positions remain completely untouched across failure cycles.
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
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
from services.opportunity_funnel_service import OpportunityFunnelService
from services.quote_validator import ValidatedQuote, normalize_execution_quote
from services.risk_settings_service import risk_settings_service
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
from tests.test_v15_1_execution_lifecycle import (
    CandleStub,
    _make_causal_long_m1_candles,
    _make_causal_short_m1_candles,
    _setup_positive_trading_policy,
)


@pytest.fixture
def isolated_db() -> Session:
    engine = create_engine("sqlite:///:memory:", echo=False)
    models.Base.metadata.create_all(engine)
    SessionFactory = sessionmaker(bind=engine)
    session = SessionFactory()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture
def base_cfg() -> M1ScalpConfig:
    return M1ScalpConfig(
        scalp_m1_mode=ScalpMode.PAPER.value,
        daily_ny_research_enabled=False,
        min_closed_m1_bars=15,
        displacement_body_atr_min=0.5,
    )


def make_fresh_quote(now_ms: int, bid: float = 2622.0, ask: float = 2622.5, last: Optional[float] = None) -> Dict[str, Any]:
    return {
        "bid": bid,
        "ask": ask,
        "last": last if last is not None else (bid + ask) / 2.0,
        "is_valid": True,
        "server_time": now_ms,
        "observed_at": now_ms,
        "received_at_ms": now_ms,
    }


def make_real_integrity_error_writer(target_zone_id: Optional[str] = None):
    real_stage = stage_hypothesis_lifecycle

    def injector(db: Session, sym: str, hyp: Any, now_ms: Optional[int] = None):
        if target_zone_id is None or (hyp.watched_zone and hyp.watched_zone.zone_id == target_zone_id):
            dup1 = models.SystemConfig(key="DUP_INJECT_KEY", value="VAL1", updated_at=int(time.time() * 1000))
            db.add(dup1)
            db.flush()
            dup2 = models.SystemConfig(key="DUP_INJECT_KEY", value="VAL2", updated_at=int(time.time() * 1000))
            db.add(dup2)
            db.flush()  # Forces real IntegrityError
        return real_stage(db, sym, hyp, now_ms=now_ms)

    return injector


# =====================================================================
# 1. REAL INTEGRITY ERROR & TRANSACTION RECOVERY
# =====================================================================

def test_v15_8_real_integrity_error_flush_recovers_without_pending_rollback_error(isolated_db: Session, base_cfg: M1ScalpConfig):
    """
    SECTIONS 025, 037-040, 143:
    When a real IntegrityError occurs during db.flush() inside lifecycle writer,
    evaluate_cycle() recovers gracefully, rolls back transaction, and returns DEGRADED.
    NO PendingRollbackError escapes to the caller.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    zone = WatchedZone(
        zone_id="z_real_integrity", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_long_m1_candles()
    now_ms = 1_300_000
    quote = make_fresh_quote(now_ms)
    feed_health = {"status": "CONNECTED", "latency_ms": 10}

    with patch("services.liquidity_scenario_service.stage_hypothesis_lifecycle", side_effect=make_real_integrity_error_writer()):
        res = orch.evaluate_cycle(
            isolated_db, "XAUUSDT",
            now_ms=now_ms, quote=quote, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
            config=base_cfg, feed_health=feed_health, auto_enabled=True,
        )

    assert res["status"] == "DEGRADED"
    assert res["transaction_status"] == "FAILED"
    assert res["rollback_status"] == "ROLLED_BACK"
    assert res["armed_order_id"] is None
    assert isolated_db.query(models.PaperOrder).count() == 0


def test_v15_8_session_usable_after_rollback_recovery(isolated_db: Session, base_cfg: M1ScalpConfig):
    """
    SECTION 040, 144:
    After rollback recovery from IntegrityError, a simple query on the session succeeds cleanly.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    zone = WatchedZone(
        zone_id="z_sess_usable", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_long_m1_candles()
    now_ms = 1_300_000
    quote = make_fresh_quote(now_ms)

    with patch("services.liquidity_scenario_service.stage_hypothesis_lifecycle", side_effect=make_real_integrity_error_writer()):
        orch.evaluate_cycle(
            isolated_db, "XAUUSDT",
            now_ms=now_ms, quote=quote, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
            config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
        )

    # Session must be functional for subsequent queries
    cfg_count = isolated_db.query(models.SystemConfig).count()
    assert cfg_count >= 0


def test_v15_8_no_queries_between_poison_and_rollback(isolated_db: Session, base_cfg: M1ScalpConfig):
    """
    SECTIONS 026, 046, 061, 145:
    When session is poisoned by flush failure, risk_settings_service.get_settings(db)
    and crud.get_or_create_today_audit(db) are NOT called.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    zone = WatchedZone(
        zone_id="z_no_query", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_long_m1_candles()
    now_ms = 1_300_000
    quote = make_fresh_quote(now_ms)

    with patch.object(risk_settings_service, "get_settings", wraps=risk_settings_service.get_settings) as spy_risk, \
         patch.object(crud, "get_or_create_today_audit", wraps=crud.get_or_create_today_audit) as spy_audit:
        with patch("services.liquidity_scenario_service.stage_hypothesis_lifecycle", side_effect=make_real_integrity_error_writer()):
            orch.evaluate_cycle(
                isolated_db, "XAUUSDT",
                now_ms=now_ms, quote=quote, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
                config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
            )

        assert spy_risk.call_count == 0
        assert spy_audit.call_count == 0


def test_v15_8_no_order_armed_and_arm_candidate_not_called(isolated_db: Session, base_cfg: M1ScalpConfig):
    """
    SECTION 146:
    _arm_paper_candidate() is NOT called when transaction failure occurs.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    zone = WatchedZone(
        zone_id="z_no_arm", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_long_m1_candles()
    now_ms = 1_300_000
    quote = make_fresh_quote(now_ms)

    with patch.object(orch, "_arm_paper_candidate", wraps=orch._arm_paper_candidate) as spy_arm:
        with patch("services.liquidity_scenario_service.stage_hypothesis_lifecycle", side_effect=make_real_integrity_error_writer()):
            res = orch.evaluate_cycle(
                isolated_db, "XAUUSDT",
                now_ms=now_ms, quote=quote, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
                config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
            )

        assert spy_arm.call_count == 0
        assert res["armed_order_id"] is None
        assert isolated_db.query(models.PaperOrder).count() == 0


def test_v15_8_watchsetup_and_snapshot_not_mutated(isolated_db: Session, base_cfg: M1ScalpConfig):
    """
    SECTION 147:
    No READY WatchSetup or provisional prices are committed from a failed cycle transaction.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    zone = WatchedZone(
        zone_id="z_no_mut", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    ws_count_before = isolated_db.query(models.WatchSetup).count()

    candles_1m = _make_causal_long_m1_candles()
    now_ms = 1_300_000
    quote = make_fresh_quote(now_ms)

    with patch("services.liquidity_scenario_service.stage_hypothesis_lifecycle", side_effect=make_real_integrity_error_writer()):
        orch.evaluate_cycle(
            isolated_db, "XAUUSDT",
            now_ms=now_ms, quote=quote, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
            config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
        )

    ws_count_after = isolated_db.query(models.WatchSetup).count()
    assert ws_count_after == ws_count_before


def test_v15_8_staged_data_before_failure_rolled_back_consistently(isolated_db: Session, base_cfg: M1ScalpConfig):
    """
    SECTIONS 066, 148:
    A staged change made in the same transaction before writer failure does NOT persist after rollback.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    zone = WatchedZone(
        zone_id="z_staged_rb", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_long_m1_candles()
    now_ms = 1_300_000
    quote = make_fresh_quote(now_ms)

    # Pre-add a staged object in isolated_db before cycle evaluation
    staged_dummy = models.SystemConfig(key="STAGED_BEFORE_FAIL", value="UNCOMMITTED", updated_at=now_ms)
    isolated_db.add(staged_dummy)

    with patch("services.liquidity_scenario_service.stage_hypothesis_lifecycle", side_effect=make_real_integrity_error_writer()):
        orch.evaluate_cycle(
            isolated_db, "XAUUSDT",
            now_ms=now_ms, quote=quote, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
            config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
        )

    # Since cycle failed and rolled back, STAGED_BEFORE_FAIL must not exist in DB
    found = isolated_db.query(models.SystemConfig).filter(models.SystemConfig.key == "STAGED_BEFORE_FAIL").first()
    assert found is None


def test_v15_8_expire_path_real_integrity_error_recovery(isolated_db: Session, base_cfg: M1ScalpConfig):
    """
    SECTIONS 060, 149:
    stage_hypothesis_status_transition() encountering real IntegrityError aborts cycle
    and recovers safely without subsequent queries on poisoned session.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    t0 = 1_000_000
    now_ms = t0 + 5 * 3600 * 1000  # Past 4h expiry

    fp = compute_hypothesis_source_fingerprint("hyp_overdue_test", "XAUUSDT", "BULLISH", "z_overdue", t0)
    payload = {
        "schema_version": 1,
        "hypothesis_id": "hyp_overdue_test",
        "symbol": "XAUUSDT",
        "direction": "BULLISH",
        "is_primary": True,
        "zone_id": "z_overdue",
        "created_at_ms": t0,
        "expiry_ms": t0 + 4 * 3600 * 1000,
        "status": "ACTIVE",
        "revision": 1,
        "target_price": 2650.0,
        "target_level_id": None,
        "target_type": "LIQUIDITY_LEVEL",
        "target_status": "CONFIRMED",
        "source_fingerprint": fp,
        "updated_at_ms": t0,
    }
    key = get_hypothesis_storage_key("XAUUSDT", "hyp_overdue_test")
    isolated_db.add(models.SystemConfig(key=key, value=json.dumps(payload), updated_at=t0))
    isolated_db.commit()

    candles_1m = _make_causal_long_m1_candles()
    quote = make_fresh_quote(now_ms)

    def expire_integrity_injector(db, sym, d_rec, desired_status="EXPIRED", now_ms=None):
        dup1 = models.SystemConfig(key="DUP_EXP_KEY", value="VAL1", updated_at=int(time.time() * 1000))
        db.add(dup1)
        db.flush()
        dup2 = models.SystemConfig(key="DUP_EXP_KEY", value="VAL2", updated_at=int(time.time() * 1000))
        db.add(dup2)
        db.flush()
        return LifecycleStageResult.FAILED

    with patch("services.liquidity_scenario_service.stage_hypothesis_status_transition", side_effect=expire_integrity_injector):
        res = orch.evaluate_cycle(
            isolated_db, "XAUUSDT",
            now_ms=now_ms, quote=quote, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
            config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
        )

    assert res["status"] == "DEGRADED"
    assert res["transaction_status"] == "FAILED"
    assert res["rollback_status"] == "ROLLED_BACK"


def test_v15_8_writer_returns_failed_outcome_aborts_consistently(isolated_db: Session, base_cfg: M1ScalpConfig):
    """
    SECTION 150:
    When writer returns LifecycleStageResult.FAILED (without exception),
    cycle aborts early, rolls back, and transaction_status is FAILED.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    zone = WatchedZone(
        zone_id="z_failed_no_ex", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_long_m1_candles()
    now_ms = 1_300_000
    quote = make_fresh_quote(now_ms)

    with patch("services.liquidity_scenario_service.stage_hypothesis_lifecycle", return_value=LifecycleStageResult.FAILED):
        res = orch.evaluate_cycle(
            isolated_db, "XAUUSDT",
            now_ms=now_ms, quote=quote, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
            config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
        )

    assert res["status"] == "DEGRADED"
    assert res["transaction_status"] == "FAILED"
    assert res["rollback_status"] == "ROLLED_BACK"
    assert res["armed_order_id"] is None


def test_v15_8_writer_blocked_is_isolated_not_global_abort(isolated_db: Session, base_cfg: M1ScalpConfig):
    """
    SECTION 043, 045, 151:
    A BLOCKED writer on one zone does NOT roll back or abort the entire cycle if database is healthy.
    Healthy zones and models continue to be evaluated.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    zone_blocked = WatchedZone(
        zone_id="z_blk", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    zone_ok = WatchedZone(
        zone_id="z_ok", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2625.0, bottom=2605.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone_blocked, zone_ok])

    candles_1m = _make_causal_long_m1_candles()
    now_ms = 1_300_000
    quote = make_fresh_quote(now_ms)

    real_stage = stage_hypothesis_lifecycle

    def selective_writer(db, sym, hyp, now_ms=None):
        if hyp.watched_zone and hyp.watched_zone.zone_id == "z_blk":
            return LifecycleStageResult.BLOCKED
        return real_stage(db, sym, hyp, now_ms=now_ms)

    with patch("services.liquidity_scenario_service.stage_hypothesis_lifecycle", side_effect=selective_writer):
        res = orch.evaluate_cycle(
            isolated_db, "XAUUSDT",
            now_ms=now_ms, quote=quote, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
            config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
        )

    # Database is healthy, transaction commits cleanly with PARTIAL status
    assert res["transaction_status"] == "COMMITTED"
    assert res["status"] in ("PARTIAL", "OK")


def test_v15_8_commit_failure_triggers_rollback(isolated_db: Session, base_cfg: M1ScalpConfig):
    """
    SECTION 152:
    Staging succeeds but db.commit() raises an exception.
    Cycle rolls back, armed_order_id is None, cache not published, status DEGRADED.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    zone = WatchedZone(
        zone_id="z_commit_fail", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_long_m1_candles()
    now_ms = 1_300_000
    quote = make_fresh_quote(now_ms)

    orig_commit = isolated_db.commit
    commit_calls = [0]

    def failing_commit():
        commit_calls[0] += 1
        if commit_calls[0] > 1:
            raise RuntimeError("Disk I/O error on cycle final commit")
        return orig_commit()

    with patch.object(isolated_db, "commit", side_effect=failing_commit):
        res = orch.evaluate_cycle(
            isolated_db, "XAUUSDT",
            now_ms=now_ms, quote=quote, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
            config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
        )

    assert res["status"] == "DEGRADED"
    assert res["transaction_status"] == "FAILED"
    assert res["rollback_status"] == "ROLLED_BACK"
    assert res["armed_order_id"] is None


def test_v15_8_rollback_failure_diagnostic_honest(isolated_db: Session, base_cfg: M1ScalpConfig):
    """
    SECTION 153:
    When db.rollback() itself fails in _finalize_failed_cycle(),
    the system reports rollback_status='ROLLBACK_FAILED' honestly without raising unhandled exception.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    zone = WatchedZone(
        zone_id="z_rb_fail", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_long_m1_candles()
    now_ms = 1_300_000
    quote = make_fresh_quote(now_ms)

    with patch.object(isolated_db, "rollback", side_effect=RuntimeError("Connection terminated on rollback")):
        with patch("services.liquidity_scenario_service.stage_hypothesis_lifecycle", return_value=LifecycleStageResult.FAILED):
            res = orch.evaluate_cycle(
                isolated_db, "XAUUSDT",
                now_ms=now_ms, quote=quote, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
                config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
            )

    assert res["status"] == "DEGRADED"
    assert res["transaction_status"] == "FAILED"
    assert res["rollback_status"] == "ROLLBACK_FAILED"


def test_v15_8_cache_cleared_after_db_failure(isolated_db: Session, base_cfg: M1ScalpConfig):
    """
    SECTION 068, 154:
    Active hypothesis cache and authority cache are cleared after transaction failure.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    zone = WatchedZone(
        zone_id="z_cache_clr", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    # Seed in-memory cache with dummy hypothesis having string hypothesis_id
    dummy_hyp = MagicMock()
    dummy_hyp.hypothesis_id = "hyp_dummy_cache"
    orch._active_hypotheses["XAUUSDT"] = [dummy_hyp]
    orch._latest_hypothesis_authority["XAUUSDT"] = MagicMock()

    candles_1m = _make_causal_long_m1_candles()
    now_ms = 1_300_000
    quote = make_fresh_quote(now_ms)

    with patch("services.liquidity_scenario_service.stage_hypothesis_lifecycle", return_value=LifecycleStageResult.FAILED):
        orch.evaluate_cycle(
            isolated_db, "XAUUSDT",
            now_ms=now_ms, quote=quote, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
            config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
        )

    assert orch.get_active_hypotheses("XAUUSDT") == []
    assert orch.get_latest_hypothesis_authority("XAUUSDT") is None


def test_v15_8_api_hypotheses_reports_degraded_not_false_healthy(isolated_db: Session, base_cfg: M1ScalpConfig):
    """
    SECTIONS 070, 155:
    When authoritative cache is cleared due to transaction failure,
    get_scalp_hypotheses_endpoint() reports data_quality='DEGRADED' rather than false 'HEALTHY'.
    """
    from main import get_scalp_hypotheses_endpoint
    from services.scalp_watcher_orchestrator import scalp_watcher_orchestrator

    _setup_positive_trading_policy(isolated_db)
    candles_1m = _make_causal_long_m1_candles()
    now_ms = 1_300_000
    quote = make_fresh_quote(now_ms)

    zone = WatchedZone(
        zone_id="z_api_deg", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    scalp_watcher_orchestrator.set_active_zones("XAUUSDT", [zone])

    with patch("services.liquidity_scenario_service.stage_hypothesis_lifecycle", return_value=LifecycleStageResult.FAILED):
        scalp_watcher_orchestrator.evaluate_cycle(
            isolated_db, "XAUUSDT",
            now_ms=now_ms, quote=quote, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
            config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
        )

    api_resp = get_scalp_hypotheses_endpoint(symbol="XAUUSDT")
    assert api_resp["data_quality"] == "DEGRADED"
    assert api_resp["count"] == 0


def test_v15_8_next_cycle_recovers_cleanly(isolated_db: Session, base_cfg: M1ScalpConfig):
    """
    SECTION 156:
    After a cycle with database failure, a subsequent cycle with a healthy database
    and fresh quote successfully evaluates and arms an order.
    """
    _setup_positive_trading_policy(isolated_db)
    orch = ScalpWatcherOrchestrator()
    zone = WatchedZone(
        zone_id="z_next_rec", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_long_m1_candles()
    now_ms = 1_300_000
    quote1 = make_fresh_quote(now_ms)
    feed_health = {"status": "CONNECTED", "latency_ms": 10}

    # Cycle 1: database failure
    with patch("services.liquidity_scenario_service.stage_hypothesis_lifecycle", return_value=LifecycleStageResult.FAILED):
        res1 = orch.evaluate_cycle(
            isolated_db, "XAUUSDT",
            now_ms=now_ms, quote=quote1, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
            config=base_cfg, feed_health=feed_health, auto_enabled=True,
        )
    assert res1["status"] == "DEGRADED"

    # Cycle 2: healthy database and fresh quote 60s later
    now_ms_2 = now_ms + 60_000
    quote2 = make_fresh_quote(now_ms_2)
    res2 = orch.evaluate_cycle(
        isolated_db, "XAUUSDT",
        now_ms=now_ms_2, quote=quote2, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
        config=base_cfg, feed_health=feed_health, auto_enabled=True,
    )

    assert res2["status"] == "OK"
    assert res2["armed_order_id"] is not None
    assert isolated_db.query(models.PaperOrder).count() == 1


def test_v15_8_existing_order_untouched_by_failure_cycle(isolated_db: Session, base_cfg: M1ScalpConfig):
    """
    SECTIONS 065, 189:
    Pre-existing committed orders are NOT altered, cancelled, or duplicated
    by subsequent cycles that suffer database persistence failures.
    """
    _setup_positive_trading_policy(isolated_db)
    # Seed committed PaperOrder
    pre_order = models.PaperOrder(
        id="ord_pre_existing_01",
        setup_id="watch_pre",
        setup_instance_id="inst_pre",
        idempotency_key="idem_pre",
        signal_id="sig_pre",
        instrument="XAUUSDT",
        direction="LONG",
        state="armed",
        order_type="MARKET",
        timeframe="1M",
        planned_entry=2620.0,
        stop_loss=2610.0,
        take_profit=2650.0,
        quantity=0.1,
        initial_risk_usdt=2.5,
        risk_pct=0.25,
        gross_rr=3.0,
        estimated_net_rr=2.8,
        leverage=30,
        margin_mode="ISOLATED",
        origin="AUTO_STRATEGY",
        created_at=1_000_000,
    )
    isolated_db.add(pre_order)
    isolated_db.commit()

    orch = ScalpWatcherOrchestrator()
    zone = WatchedZone(
        zone_id="z_pre_ord_test", symbol="XAUUSDT", timeframe="15M", zone_type="FVG",
        direction="BULLISH", top=2620.0, bottom=2600.0, created_at=60_000, known_at=60_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones("XAUUSDT", [zone])

    candles_1m = _make_causal_long_m1_candles()
    now_ms = 1_300_000
    quote = make_fresh_quote(now_ms)

    with patch("services.liquidity_scenario_service.stage_hypothesis_lifecycle", return_value=LifecycleStageResult.FAILED):
        res = orch.evaluate_cycle(
            isolated_db, "XAUUSDT",
            now_ms=now_ms, quote=quote, candles_1m=candles_1m, candles_5m=[], candles_15m=[],
            config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True,
        )

    # Check pre-existing order is exactly intact
    reloaded = isolated_db.query(models.PaperOrder).filter(models.PaperOrder.id == "ord_pre_existing_01").first()
    assert reloaded is not None
    assert reloaded.state == "armed"
    assert reloaded.stop_loss == 2610.0
    assert reloaded.take_profit == 2650.0
    assert isolated_db.query(models.PaperOrder).count() == 1
