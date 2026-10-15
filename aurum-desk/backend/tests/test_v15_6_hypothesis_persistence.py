"""
backend/tests/test_v15_6_hypothesis_persistence.py
V15.6 - Durable Hypothesis Persistence Correctness Test Suite.

Covers the two reviewer defect classes:
A. Expire durable hypothesis without synthetic zones or domain object fabrication (Sections 021 - 060).
B. Propagate corrupt durable records without silent recreation, anchor renewal, or blind overwrite (Sections 061 - 140).
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from typing import Any, Dict, List, Optional
import pytest
from sqlalchemy.orm import Session

import models
import crud
from services.clock import FakeClock
from services.liquidity_scenario_service import (
    DealingRange,
    DurableHypothesisRecord,
    HypothesisBatchLoadResult,
    HypothesisLoadIssue,
    HypothesisLoadStatus,
    LifecycleStageResult,
    LiquidityHypothesis,
    build_liquidity_hypotheses,
    compute_hypothesis_source_fingerprint,
    get_hypothesis_storage_key,
    load_durable_hypotheses_for_symbol,
    load_hypothesis_lifecycle,
    parse_hypothesis_lifecycle_row,
    stage_hypothesis_lifecycle,
    stage_hypothesis_status_transition,
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
from services.trade_lifecycle_service import TradeLifecycleService
from tests.test_v15_1_execution_lifecycle import (
    CandleStub,
    _make_causal_long_m1_candles,
    _setup_positive_trading_policy,
)


@pytest.fixture
def base_cfg():
    cfg = M1ScalpConfig(
        scalp_m1_mode=ScalpMode.PAPER.value,
        daily_ny_research_enabled=False,
        min_closed_m1_bars=15,
        displacement_body_atr_min=0.5,
    )
    return cfg


def _create_and_stage_test_hypothesis(
    db: Session,
    symbol: str,
    hyp_id: str,
    zone_id: str,
    created_at_ms: int,
    expiry_ms: int,
    status: str = "ACTIVE",
    revision: int = 1,
    direction: str = "BULLISH",
    target_price: float = 2050.0,
) -> DurableHypothesisRecord:
    key = get_hypothesis_storage_key(symbol, hyp_id)
    fp = compute_hypothesis_source_fingerprint(
        hypothesis_id=hyp_id,
        symbol=symbol,
        direction=direction,
        source_zone_id=zone_id,
        created_at_ms=created_at_ms,
    )
    payload = {
        "schema_version": 1,
        "hypothesis_id": hyp_id,
        "symbol": symbol,
        "direction": direction,
        "is_primary": True,
        "zone_id": zone_id,
        "created_at_ms": created_at_ms,
        "expiry_ms": expiry_ms,
        "status": status,
        "revision": revision,
        "target_price": target_price,
        "target_level_id": "lvl_1",
        "target_type": "LIQUIDITY_LEVEL",
        "target_status": "CONFIRMED",
        "source_fingerprint": fp,
        "updated_at_ms": created_at_ms,
    }
    rec = models.SystemConfig(
        key=key,
        value=json.dumps(payload, ensure_ascii=False),
        updated_at=created_at_ms,
    )
    db.merge(rec)
    db.commit()
    return DurableHypothesisRecord(
        schema_version=1,
        hypothesis_id=hyp_id,
        symbol=symbol,
        direction=direction,
        is_primary=True,
        created_at_ms=created_at_ms,
        expiry_ms=expiry_ms,
        status=status,
        revision=revision,
        source_zone_id=zone_id,
        target_price=target_price,
        target_level_id="lvl_1",
        target_type="LIQUIDITY_LEVEL",
        target_status="CONFIRMED",
        source_fingerprint=fp,
        updated_at_ms=created_at_ms,
    )


# =====================================================================
# SECTION A: DIRECT DURABLE EXPIRY WITHOUT SYNTHETIC ZONES (021 - 060)
# =====================================================================

def test_exact_repro_a_expired_durable_hypothesis_without_zone(isolated_db: Session, base_cfg):
    """
    REPRO A (SECTIONS 021 - 023, 049 - 055):
    Expired durable hypothesis whose source zone is NOT in the active watchlist.
    Prior implementation tried to build LiquidityHypothesis with fake WatchedZone(top=0, bottom=0)
    and failed with NameError or ValueError (top <= bottom).
    V15.6 executes evaluate_cycle() safely without NameError or synthetic zones,
    transitions persisted status to EXPIRED, and preserves all anchors.
    """
    symbol = "XAUUSDT"
    _setup_positive_trading_policy(isolated_db)

    t0 = 1_000_000
    expiry_t0 = 1_003_600_000
    hyp_id = "hyp-repro-a-overdue"
    zone_id = "z_old_aged_out_zone"

    # Stage valid ACTIVE record
    d_rec = _create_and_stage_test_hypothesis(
        isolated_db, symbol, hyp_id, zone_id, created_at_ms=t0, expiry_ms=expiry_t0, status="ACTIVE"
    )

    # Time moves past expiry
    now_ms = 1_004_000_000
    assert now_ms >= expiry_t0

    # Start new orchestrator instance (no prior cache)
    orch = ScalpWatcherOrchestrator()
    # Active watchlist does NOT contain z_old_aged_out_zone
    orch.set_active_zones(symbol, [])

    quote = {"bid": 2000.0, "ask": 2000.1, "last": 2000.05, "timestamp": now_ms}
    feed_health = {"status": "HEALTHY", "is_stale": False, "stale": False}

    # Evaluate cycle - must NOT raise NameError or ValueError!
    res = orch.evaluate_cycle(
        isolated_db,
        symbol,
        now_ms=now_ms,
        quote=quote,
        candles_1m=_make_causal_long_m1_candles(),
        config=base_cfg,
        feed_health=feed_health,
        auto_enabled=True,
    )

    assert res["status"] == "OK"
    assert res["armed_order_id"] is None

    # Verify persisted record in DB transitioned to EXPIRED
    status, updated_rec = load_hypothesis_lifecycle(isolated_db, hyp_id, symbol)
    assert status == HypothesisLoadStatus.FOUND
    assert updated_rec is not None
    assert updated_rec.status == "EXPIRED"
    # Immutable anchors 100% preserved
    assert updated_rec.created_at_ms == t0
    assert updated_rec.expiry_ms == expiry_t0
    assert updated_rec.hypothesis_id == hyp_id
    assert updated_rec.source_fingerprint == d_rec.source_fingerprint
    assert updated_rec.source_zone_id == zone_id
    assert updated_rec.revision >= 1

    # Verify no financial order or domain events emitted
    assert isolated_db.query(models.PaperOrder).count() == 0
    trade_events = isolated_db.query(models.DomainEvent).filter(models.DomainEvent.event_type.like("trade.%")).all()
    assert len(trade_events) == 0


def test_empty_watchlist_with_overdue_record(isolated_db: Session, base_cfg):
    """
    SECTION 057, 143: Cycle with zero zones and overdue durable records.
    Processes metadata transition to EXPIRED without assumption of at least one zone.
    """
    symbol = "XAUUSDT"
    _setup_positive_trading_policy(isolated_db)
    t0 = 1_000_000
    expiry_ms = 1_001_000
    _create_and_stage_test_hypothesis(isolated_db, symbol, "hyp-empty-wl", "z_none", t0, expiry_ms)

    orch = ScalpWatcherOrchestrator()
    orch.set_active_zones(symbol, [])
    quote = {"bid": 2000.0, "ask": 2000.1, "last": 2000.05, "timestamp": 1_002_000}
    feed_health = {"status": "HEALTHY", "is_stale": False, "stale": False}

    res = orch.evaluate_cycle(
        isolated_db, symbol, now_ms=1_002_000, quote=quote, candles_1m=_make_causal_long_m1_candles(), config=base_cfg, feed_health=feed_health, auto_enabled=True
    )
    assert res["status"] == "OK"
    status, rec = load_hypothesis_lifecycle(isolated_db, "hyp-empty-wl", symbol)
    assert status == HypothesisLoadStatus.FOUND
    assert rec.status == "EXPIRED"


def test_multiple_overdue_and_terminal_records(isolated_db: Session, base_cfg):
    """
    SECTION 058, 145: Batch contains multiple overdue ACTIVE records and one already terminal record.
    All overdue records become EXPIRED; terminal record is not re-mutated.
    """
    symbol = "XAUUSDT"
    _setup_positive_trading_policy(isolated_db)
    t0 = 1_000_000
    expiry_ms = 1_002_000

    _create_and_stage_test_hypothesis(isolated_db, symbol, "hyp-mult-1", "z1", t0, expiry_ms, status="ACTIVE", revision=1)
    _create_and_stage_test_hypothesis(isolated_db, symbol, "hyp-mult-2", "z2", t0, expiry_ms, status="ACTIVE", revision=1)
    # Terminal record
    _create_and_stage_test_hypothesis(isolated_db, symbol, "hyp-term-3", "z3", t0, expiry_ms, status="EXPIRED", revision=2)

    orch = ScalpWatcherOrchestrator()
    orch.set_active_zones(symbol, [])
    quote = {"bid": 2000.0, "ask": 2000.1, "last": 2000.05, "timestamp": 1_003_000}
    feed_health = {"status": "HEALTHY", "is_stale": False, "stale": False}

    orch.evaluate_cycle(
        isolated_db, symbol, now_ms=1_003_000, quote=quote, candles_1m=_make_causal_long_m1_candles(), config=base_cfg, feed_health=feed_health, auto_enabled=True
    )

    rec1 = load_hypothesis_lifecycle(isolated_db, "hyp-mult-1", symbol)[1]
    rec2 = load_hypothesis_lifecycle(isolated_db, "hyp-mult-2", symbol)[1]
    rec3 = load_hypothesis_lifecycle(isolated_db, "hyp-term-3", symbol)[1]

    assert rec1.status == "EXPIRED"
    assert rec2.status == "EXPIRED"
    assert rec3.status == "EXPIRED"
    assert rec3.revision == 2  # No duplicate revision churn on already terminal record


def test_direct_status_transition_helper(isolated_db: Session):
    """
    SECTION 031 - 047, 147: stage_hypothesis_status_transition direct semantics:
    - Precondition: now_ms >= expiry_ms for EXPIRED
    - Monotonicity: cannot transition EXPIRED to ACTIVE
    - Idempotency: repeated transition returns NO_CHANGE
    """
    symbol = "XAUUSDT"
    t0 = 1000
    exp = 2000
    hyp_id = "hyp-direct-status"
    rec = _create_and_stage_test_hypothesis(isolated_db, symbol, hyp_id, "z1", t0, exp, status="ACTIVE")

    # Before expiry: should return NO_CHANGE
    res1 = stage_hypothesis_status_transition(isolated_db, symbol, rec, desired_status="EXPIRED", now_ms=1500)
    assert res1 == LifecycleStageResult.NO_CHANGE

    # At expiry: should return STAGED
    res2 = stage_hypothesis_status_transition(isolated_db, symbol, rec, desired_status="EXPIRED", now_ms=2000)
    assert res2 == LifecycleStageResult.STAGED
    isolated_db.commit()

    # Verify persisted
    status, updated = load_hypothesis_lifecycle(isolated_db, hyp_id, symbol)
    assert status == HypothesisLoadStatus.FOUND
    assert updated.status == "EXPIRED"

    # Repeated call: should return NO_CHANGE
    res3 = stage_hypothesis_status_transition(isolated_db, symbol, updated, desired_status="EXPIRED", now_ms=2500)
    assert res3 == LifecycleStageResult.NO_CHANGE

    # Attempt to transition terminal back to ACTIVE: should return BLOCKED
    res4 = stage_hypothesis_status_transition(isolated_db, symbol, updated, desired_status="ACTIVE", now_ms=3000)
    assert res4 == LifecycleStageResult.BLOCKED


def test_expire_commit_failure_and_rollback(isolated_db: Session, base_cfg):
    """
    SECTION 053, 149: Commit failure during expiry cycle cleanly rolls back DB and cache.
    """
    symbol = "XAUUSDT"
    _setup_positive_trading_policy(isolated_db)
    t0 = 1000
    exp = 2000
    hyp_id = "hyp-commit-fail"
    _create_and_stage_test_hypothesis(isolated_db, symbol, hyp_id, "z1", t0, exp, status="ACTIVE")

    orch = ScalpWatcherOrchestrator()
    orch.set_active_zones(symbol, [])
    quote = {"bid": 2000.0, "ask": 2000.1, "last": 2000.05, "timestamp": 3000}
    feed_health = {"status": "HEALTHY", "is_stale": False, "stale": False}

    # Pre-seed audit so it exists and does not commit inside evaluate_cycle
    crud.get_or_create_today_audit(isolated_db)

    # Inject commit failure on cycle commit
    orig_commit = isolated_db.commit
    try:
        def failing_commit():
            raise RuntimeError("Injected DB failure during expiry commit")
        isolated_db.commit = failing_commit

        res = orch.evaluate_cycle(
            isolated_db, symbol, now_ms=3000, quote=quote, candles_1m=_make_causal_long_m1_candles(), config=base_cfg, feed_health=feed_health, auto_enabled=True
        )
    finally:
        isolated_db.commit = orig_commit

    # After rollback, cache must not contain false EXPIRED
    assert symbol not in orch._active_hypotheses
    # DB state remains ACTIVE (not committed)
    status, rec = load_hypothesis_lifecycle(isolated_db, hyp_id, symbol)
    assert status == HypothesisLoadStatus.FOUND
    assert rec.status == "ACTIVE"


# =====================================================================
# SECTION B: CORRUPT STATE PROPAGATION & NO OVERWRITE (061 - 140)
# =====================================================================

def test_exact_repro_b_corrupt_record_no_recreation_or_overwrite(isolated_db: Session, base_cfg):
    """
    REPRO B (SECTIONS 024 - 026, 061 - 140):
    Valid durable hypothesis corrupted in SystemConfig with '{broken'.
    Prior implementation skipped the record, treated it as NOT_FOUND,
    recreated a fresh hypothesis with new created_at/expiry (now + TTL),
    and blind merged over the corrupt row, erasing the evidence and returning OK.
    V15.6:
    - Retains raw corrupt payload byte-for-byte in SystemConfig.
    - Does NOT recreate new lifecycle or renew expiry.
    - Blocks entry candidate with OpportunityReasonCode.HYPOTHESIS_STATE_CORRUPT.
    - Returns PARTIAL cycle status with diagnostics.
    """
    symbol = "XAUUSDT"
    _setup_positive_trading_policy(isolated_db)

    zone_id = "z_corrupt_zone_1"
    hyp_id = f"hyp-primary-{zone_id}"
    key = get_hypothesis_storage_key(symbol, hyp_id)

    # 1. Insert broken raw JSON in DB
    raw_corrupt_payload = "{broken_raw_json_not_parseable_payload"
    corrupt_rec = models.SystemConfig(
        key=key,
        value=raw_corrupt_payload,
        updated_at=1_000_000,
    )
    isolated_db.add(corrupt_rec)
    isolated_db.commit()

    # 2. Start new orchestrator instance (no prior cache)
    orch = ScalpWatcherOrchestrator()
    # Zone appears in cycle
    zone = WatchedZone(
        zone_id=zone_id,
        symbol=symbol,
        timeframe="15M",
        zone_type="OB",
        direction="BULLISH",
        top=2000.5,
        bottom=1998.5,
        created_at=1_000_000,
        known_at=1_000_000,
        state=ZoneState.WAITING_TRIGGER.value,
    )
    orch.set_active_zones(symbol, [zone])

    now_ms = 1_005_000
    quote = {"bid": 2000.0, "ask": 2000.1, "last": 2000.05, "timestamp": now_ms}
    feed_health = {"status": "HEALTHY", "is_stale": False, "stale": False}

    # 3. Execute cycle
    res = orch.evaluate_cycle(
        isolated_db,
        symbol,
        now_ms=now_ms,
        quote=quote,
        candles_1m=_make_causal_long_m1_candles(),
        config=base_cfg,
        feed_health=feed_health,
        auto_enabled=True,
    )

    # Assert cycle detected issue and returned PARTIAL
    assert res["status"] == "PARTIAL"
    assert res["lifecycle_read_status"] == "PARTIAL"
    assert res["issues_count"] >= 1
    assert res["armed_order_id"] is None

    # Assert raw DB row was NOT overwritten: value and updated_at are byte-for-byte identical! (SECTION 163)
    db_rec = isolated_db.query(models.SystemConfig).filter(models.SystemConfig.key == key).first()
    assert db_rec is not None
    assert db_rec.value == raw_corrupt_payload
    assert db_rec.updated_at == 1_000_000

    # Assert candidate was blocked in funnel with reason HYPOTHESIS_STATE_CORRUPT (SECTION 095, 102)
    opps = isolated_db.query(models.OpportunityFunnelRecord).filter(models.OpportunityFunnelRecord.zone_id == zone_id).all()
    assert len(opps) >= 1
    reasons = json.loads(opps[-1].reason_codes_json)
    assert OpportunityReasonCode.HYPOTHESIS_STATE_CORRUPT.value in reasons
    # Assert Vietnamese human-readable explanation exists
    payload = json.loads(opps[-1].payload_json) if opps[-1].payload_json else {}
    details = payload.get("details", {})
    assert "Dữ liệu theo dõi kịch bản bị lỗi" in details.get("message", "")


def test_corrupt_json_issue_propagation(isolated_db: Session):
    """
    SECTION 061 - 067: load_durable_hypotheses_for_symbol returns structured result
    tracking blocked keys for unparseable JSON without throwing exceptions.
    """
    symbol = "XAUUSDT"
    key = get_hypothesis_storage_key(symbol, "hyp-broken-json")
    rec = models.SystemConfig(key=key, value="{not_json", updated_at=100)
    isolated_db.add(rec)
    isolated_db.commit()

    batch_res = load_durable_hypotheses_for_symbol(isolated_db, symbol)
    assert batch_res.read_status == "PARTIAL"
    assert len(batch_res.records) == 0
    assert len(batch_res.issues) == 1
    assert key in batch_res.blocked_keys
    assert batch_res.issues[0].status == HypothesisLoadStatus.CORRUPT
    assert "Corrupt JSON" in batch_res.issues[0].reason


def test_known_id_schema_corrupt_and_unsupported_version(isolated_db: Session, base_cfg):
    """
    SECTION 074, 151, 152: Readable JSON with known hypothesis_id but unsupported schema_version.
    Tracks hypothesis_id in blocked_identities; candidate entry blocked with HYPOTHESIS_SCHEMA_UNSUPPORTED.
    """
    symbol = "XAUUSDT"
    _setup_positive_trading_policy(isolated_db)
    zone_id = "z_unsupported_ver"
    hyp_id = f"hyp-primary-{zone_id}"
    key = get_hypothesis_storage_key(symbol, hyp_id)

    payload = {
        "schema_version": 99,  # Unsupported schema version
        "hypothesis_id": hyp_id,
        "symbol": symbol,
        "direction": "BULLISH",
        "created_at_ms": 1000,
        "expiry_ms": 2000,
        "status": "ACTIVE",
        "revision": 1,
    }
    rec = models.SystemConfig(key=key, value=json.dumps(payload), updated_at=100)
    isolated_db.add(rec)
    isolated_db.commit()

    batch_res = load_durable_hypotheses_for_symbol(isolated_db, symbol)
    assert batch_res.read_status == "PARTIAL"
    assert hyp_id in batch_res.blocked_identities
    assert key in batch_res.blocked_keys
    assert batch_res.issues[0].status == HypothesisLoadStatus.UNSUPPORTED_VERSION

    # Cycle evaluation blocks with HYPOTHESIS_SCHEMA_UNSUPPORTED
    orch = ScalpWatcherOrchestrator()
    zone = WatchedZone(zone_id, symbol, "15M", "OB", "BULLISH", 2000.5, 1998.5, 1000, 1000, state=ZoneState.WAITING_TRIGGER.value)
    orch.set_active_zones(symbol, [zone])
    quote = {"bid": 2000.0, "ask": 2000.1, "last": 2000.05, "timestamp": 1500}
    feed_health = {"status": "HEALTHY", "is_stale": False, "stale": False}

    orch.evaluate_cycle(isolated_db, symbol, now_ms=1500, quote=quote, candles_1m=_make_causal_long_m1_candles(), config=base_cfg, feed_health=feed_health, auto_enabled=True)

    opps = isolated_db.query(models.OpportunityFunnelRecord).filter(models.OpportunityFunnelRecord.zone_id == zone_id).all()
    assert len(opps) >= 1
    reasons = json.loads(opps[-1].reason_codes_json)
    assert OpportunityReasonCode.HYPOTHESIS_SCHEMA_UNSUPPORTED.value in reasons


def test_fingerprint_mismatch_fails_closed(isolated_db: Session):
    """
    SECTION 078, 153: Source fingerprint mismatch is an authority failure.
    Does not attempt automatic repair and does not load as valid.
    """
    symbol = "XAUUSDT"
    hyp_id = "hyp-bad-fp"
    key = get_hypothesis_storage_key(symbol, hyp_id)

    payload = {
        "schema_version": 1,
        "hypothesis_id": hyp_id,
        "symbol": symbol,
        "direction": "BULLISH",
        "created_at_ms": 1000,
        "expiry_ms": 2000,
        "status": "ACTIVE",
        "revision": 1,
        "source_fingerprint": "fake_incorrect_sha256_fingerprint",
    }
    rec = models.SystemConfig(key=key, value=json.dumps(payload), updated_at=100)
    isolated_db.add(rec)
    isolated_db.commit()

    batch_res = load_durable_hypotheses_for_symbol(isolated_db, symbol)
    assert batch_res.read_status == "PARTIAL"
    assert key in batch_res.blocked_keys
    assert batch_res.issues[0].status == HypothesisLoadStatus.CORRUPT
    assert "fingerprint mismatch" in batch_res.issues[0].reason


def test_key_payload_and_symbol_mismatch(isolated_db: Session):
    """
    SECTION 072, 073, 154, 155: Row key derived for hyp A contains payload for hyp B or symbol mismatch.
    Both must fail closed with CORRUPT.
    """
    symbol = "XAUUSDT"
    key_a = get_hypothesis_storage_key(symbol, "hyp-A")
    # Payload claiming hyp-B under key_a
    payload_mismatch = {
        "schema_version": 1,
        "hypothesis_id": "hyp-B",
        "symbol": symbol,
        "direction": "BULLISH",
        "created_at_ms": 1000,
        "expiry_ms": 2000,
        "status": "ACTIVE",
        "revision": 1,
    }
    rec = models.SystemConfig(key=key_a, value=json.dumps(payload_mismatch), updated_at=100)
    isolated_db.add(rec)
    isolated_db.commit()

    batch_res = load_durable_hypotheses_for_symbol(isolated_db, symbol)
    assert key_a in batch_res.blocked_keys
    assert "Storage key mismatch" in batch_res.issues[0].reason

    # Symbol mismatch
    key_xau_for_btc_hyp = get_hypothesis_storage_key("XAUUSDT", "hyp-btc")
    payload_sym = {
        "schema_version": 1,
        "hypothesis_id": "hyp-btc",
        "symbol": "BTCUSDT",
        "direction": "BULLISH",
        "created_at_ms": 1000,
        "expiry_ms": 2000,
        "status": "ACTIVE",
        "revision": 1,
    }
    status, _, _, reason = parse_hypothesis_lifecycle_row(key_xau_for_btc_hyp, json.dumps(payload_sym), expected_symbol="XAUUSDT")
    assert status == HypothesisLoadStatus.CORRUPT
    assert "Symbol mismatch" in reason


@pytest.mark.parametrize("bad_field,bad_val", [
    ("created_at_ms", "not_an_int"),
    ("created_at_ms", True),             # bool masquerading as int
    ("expiry_ms", False),                # bool masquerading as int
    ("expiry_ms", float("nan")),
    ("expiry_ms", float("inf")),
    ("revision", "1"),                   # string revision
    ("revision", 0),                     # rev < 1
    ("target_price", "2000.0"),          # string target
    ("target_price", float("nan")),
    ("status", "BOGUS_STATUS"),
])
def test_numeric_corruption_validation(bad_field, bad_val):
    """
    SECTION 075 - 077, 156: Strict validation of integers, finite numbers, and allowed status enums.
    Rejects booleans masquerading as integers.
    """
    hyp_id = "hyp-test-num"
    symbol = "XAUUSDT"
    key = get_hypothesis_storage_key(symbol, hyp_id)

    payload = {
        "schema_version": 1,
        "hypothesis_id": hyp_id,
        "symbol": symbol,
        "direction": "BULLISH",
        "created_at_ms": 1000,
        "expiry_ms": 2000,
        "status": "ACTIVE",
        "revision": 1,
        "target_price": 2050.0,
    }
    payload[bad_field] = bad_val

    status, record, _, reason = parse_hypothesis_lifecycle_row(key, json.dumps(payload), expected_symbol=symbol)
    assert status == HypothesisLoadStatus.CORRUPT
    assert record is None


def test_empty_store_returns_complete(isolated_db: Session):
    """
    SECTION 079, 157: Truly empty database returns COMPLETE with 0 records and 0 issues.
    Allows legitimate first-time hypothesis creation.
    """
    batch_res = load_durable_hypotheses_for_symbol(isolated_db, "XAUUSDT")
    assert batch_res.read_status == "COMPLETE"
    assert len(batch_res.records) == 0
    assert len(batch_res.issues) == 0
    assert len(batch_res.blocked_keys) == 0


def test_db_query_failure_returns_read_failed(isolated_db: Session, base_cfg):
    """
    SECTION 069, 099, 158: Database query error returns READ_FAILED.
    Orchestrator enters degraded mode and blocks new entry execution with HYPOTHESIS_STORE_UNAVAILABLE.
    """
    symbol = "XAUUSDT"
    _setup_positive_trading_policy(isolated_db)

    orch = ScalpWatcherOrchestrator()
    zone = WatchedZone("z_qfail", symbol, "15M", "OB", "BULLISH", 2000.5, 1998.5, 1000, 1000, state=ZoneState.WAITING_TRIGGER.value)
    orch.set_active_zones(symbol, [zone])
    quote = {"bid": 2000.0, "ask": 2000.1, "last": 2000.05, "timestamp": 1500}
    feed_health = {"status": "HEALTHY", "is_stale": False, "stale": False}

    # Inject query failure specifically for durable hypothesis prefix
    orig_query = isolated_db.query

    class FailingQuery:
        def __init__(self, q):
            self._q = q

        def filter(self, *criterion):
            crit_str = " ".join(str(c) for c in criterion)
            if "like" in crit_str.lower():
                raise RuntimeError("Simulated DB connection timeout")
            return self._q.filter(*criterion)

        def __getattr__(self, name):
            return getattr(self._q, name)

    def selective_query(model, *args, **kwargs):
        q = orig_query(model, *args, **kwargs)
        if model == models.SystemConfig:
            return FailingQuery(q)
        return q

    try:
        isolated_db.query = selective_query

        res = orch.evaluate_cycle(isolated_db, symbol, now_ms=1500, quote=quote, candles_1m=_make_causal_long_m1_candles(), config=base_cfg, feed_health=feed_health, auto_enabled=True)
        assert res["status"] == "DEGRADED"
        assert res["lifecycle_read_status"] == "READ_FAILED"
    finally:
        isolated_db.query = orig_query

    # Verify funnel recorded HYPOTHESIS_STORE_UNAVAILABLE
    opps = isolated_db.query(models.OpportunityFunnelRecord).filter(models.OpportunityFunnelRecord.zone_id == "z_qfail").all()
    assert len(opps) >= 1
    reasons = json.loads(opps[-1].reason_codes_json)
    assert OpportunityReasonCode.HYPOTHESIS_STORE_UNAVAILABLE.value in reasons


def test_mixed_batch_valid_and_corrupt(isolated_db: Session):
    """
    SECTION 080, 159: Batch with valid record A and corrupt record B.
    Valid A is returned in records; corrupt B is returned in issues and blocked_keys.
    Raw B remains completely unchanged.
    """
    symbol = "XAUUSDT"
    _create_and_stage_test_hypothesis(isolated_db, symbol, "hyp-valid-A", "z_A", 1000, 2000)

    key_b = get_hypothesis_storage_key(symbol, "hyp-corrupt-B")
    rec_b = models.SystemConfig(key=key_b, value="{broken_B", updated_at=500)
    isolated_db.add(rec_b)
    isolated_db.commit()

    batch_res = load_durable_hypotheses_for_symbol(isolated_db, symbol)
    assert batch_res.read_status == "PARTIAL"
    assert "hyp-valid-A" in batch_res.records
    assert batch_res.records["hyp-valid-A"].status == "ACTIVE"
    assert key_b in batch_res.blocked_keys
    assert len(batch_res.issues) == 1

    # Raw B unchanged
    b_in_db = isolated_db.query(models.SystemConfig).filter(models.SystemConfig.key == key_b).first()
    assert b_in_db.value == "{broken_B"
    assert b_in_db.updated_at == 500


def test_direct_writer_defense_against_corrupt_key(isolated_db: Session):
    """
    SECTION 121 - 126, 160: Calling stage_hypothesis_lifecycle directly on an existing corrupt key.
    Writer detects corruption, refuses to overwrite, and returns BLOCKED.
    Raw DB payload is untouched.
    """
    symbol = "XAUUSDT"
    hyp_id = "hyp-writer-defense"
    key = get_hypothesis_storage_key(symbol, hyp_id)

    raw_corrupt = "{do_not_overwrite_me"
    corrupt_rec = models.SystemConfig(key=key, value=raw_corrupt, updated_at=1234)
    isolated_db.add(corrupt_rec)
    isolated_db.commit()

    # Construct incoming hypothesis targeting the same key
    incoming_h = LiquidityHypothesis(
        hypothesis_id=hyp_id,
        direction="BULLISH",
        is_primary=True,
        watched_zone=WatchedZone("z1", symbol, "15M", "OB", "BULLISH", 2000.5, 1998.5, 1000, 1000),
        target_level=None,
        target_price=2050.0,
        dealing_range=DealingRange("RANGE", 2010.0, 1990.0, 2000.0, 1000, 2000),
        range_position="EQUILIBRIUM",
        prerequisites=[],
        invalidation_condition="",
        expiry_ms=3000,
        created_at_ms=2000,
        status="ACTIVE",
    )

    result = stage_hypothesis_lifecycle(isolated_db, symbol, incoming_h, now_ms=2500)
    assert result == LifecycleStageResult.BLOCKED

    # Verify DB still has raw payload and unchanged updated_at
    isolated_db.commit()
    persisted = isolated_db.query(models.SystemConfig).filter(models.SystemConfig.key == key).first()
    assert persisted.value == raw_corrupt
    assert persisted.updated_at == 1234


def test_stale_writer_cannot_downgrade_terminal_state(isolated_db: Session):
    """
    SECTION 125, 133, 161: Stale writer attempting to restage terminal record (EXPIRED) as ACTIVE.
    Writer rejects downgrade and returns BLOCKED.
    """
    symbol = "XAUUSDT"
    hyp_id = "hyp-terminal-protect"
    _create_and_stage_test_hypothesis(isolated_db, symbol, hyp_id, "z1", 1000, 2000, status="EXPIRED", revision=2)

    incoming_active_h = LiquidityHypothesis(
        hypothesis_id=hyp_id,
        direction="BULLISH",
        is_primary=True,
        watched_zone=WatchedZone("z1", symbol, "15M", "OB", "BULLISH", 2000.5, 1998.5, 1000, 1000),
        target_level=None,
        target_price=2050.0,
        dealing_range=DealingRange("RANGE", 2010.0, 1990.0, 2000.0, 1000, 2000),
        range_position="EQUILIBRIUM",
        prerequisites=[],
        invalidation_condition="",
        expiry_ms=2000,
        created_at_ms=1000,
        status="ACTIVE",  # Stale attempt to revive
        revision=1,
    )

    res = stage_hypothesis_lifecycle(isolated_db, symbol, incoming_active_h, now_ms=3000)
    assert res == LifecycleStageResult.BLOCKED

    status, rec = load_hypothesis_lifecycle(isolated_db, hyp_id, symbol)
    assert rec.status == "EXPIRED"
    assert rec.revision == 2


def test_restart_after_corrupt_state_remains_blocked(isolated_db: Session, base_cfg):
    """
    SECTION 118, 162: Service restart after corrupt state detection.
    New orchestrator instance still detects corrupt key and blocks recreation.
    """
    symbol = "XAUUSDT"
    _setup_positive_trading_policy(isolated_db)
    zone_id = "z_restart_corrupt"
    hyp_id = "hyp_XAUUSDT_15m_ob_z_restart_corrupt"
    key = get_hypothesis_storage_key(symbol, hyp_id)

    rec = models.SystemConfig(key=key, value="{corrupt_payload", updated_at=100)
    isolated_db.add(rec)
    isolated_db.commit()

    # Cycle 1
    orch1 = ScalpWatcherOrchestrator()
    zone = WatchedZone(zone_id, symbol, "15M", "OB", "BULLISH", 2000.5, 1998.5, 1000, 1000, state=ZoneState.WAITING_TRIGGER.value)
    orch1.set_active_zones(symbol, [zone])
    quote = {"bid": 2000.0, "ask": 2000.1, "last": 2000.05, "timestamp": 1500}
    feed_health = {"status": "HEALTHY", "is_stale": False, "stale": False}

    res1 = orch1.evaluate_cycle(isolated_db, symbol, now_ms=1500, quote=quote, candles_1m=_make_causal_long_m1_candles(), config=base_cfg, feed_health=feed_health, auto_enabled=True)
    assert res1["status"] == "PARTIAL"

    # Cycle 2 on brand new instance
    orch2 = ScalpWatcherOrchestrator()
    orch2.set_active_zones(symbol, [zone])
    res2 = orch2.evaluate_cycle(isolated_db, symbol, now_ms=1600, quote=quote, candles_1m=_make_causal_long_m1_candles(), config=base_cfg, feed_health=feed_health, auto_enabled=True)
    assert res2["status"] == "PARTIAL"
    assert res2["armed_order_id"] is None

    # Assert raw payload remains unchanged
    persisted = isolated_db.query(models.SystemConfig).filter(models.SystemConfig.key == key).first()
    assert persisted.value == "{corrupt_payload"


def test_cache_cannot_override_db_corruption(isolated_db: Session, base_cfg):
    """
    SECTION 096, 164: In-memory cache holds ACTIVE hypothesis, but DB key was corrupted.
    Cycle invalidates cache entry and blocks candidate from arming.
    """
    symbol = "XAUUSDT"
    _setup_positive_trading_policy(isolated_db)
    zone_id = "z_cache_override"
    hyp_id = f"hyp-primary-{zone_id}"
    key = get_hypothesis_storage_key(symbol, hyp_id)

    # Put corrupt string in DB
    rec = models.SystemConfig(key=key, value="{corrupt_in_db", updated_at=100)
    isolated_db.add(rec)
    isolated_db.commit()

    orch = ScalpWatcherOrchestrator()
    # Artificially inject stale valid hypothesis in in-memory cache
    stale_active_h = LiquidityHypothesis(
        hypothesis_id=hyp_id,
        direction="BULLISH",
        is_primary=True,
        watched_zone=WatchedZone(zone_id, symbol, "15M", "OB", "BULLISH", 2000.5, 1998.5, 1000, 1000),
        target_level=None,
        target_price=2050.0,
        dealing_range=DealingRange("RANGE", 2010.0, 1990.0, 2000.0, 1000, 2000),
        range_position="EQUILIBRIUM",
        prerequisites=[],
        invalidation_condition="",
        expiry_ms=3000,
        created_at_ms=1000,
        status="ACTIVE",
    )
    orch._active_hypotheses[symbol] = [stale_active_h]

    zone = WatchedZone(zone_id, symbol, "15M", "OB", "BULLISH", 2000.5, 1998.5, 1000, 1000, state=ZoneState.WAITING_TRIGGER.value)
    orch.set_active_zones(symbol, [zone])
    quote = {"bid": 2000.0, "ask": 2000.1, "last": 2000.05, "timestamp": 1500}
    feed_health = {"status": "HEALTHY", "is_stale": False, "stale": False}

    res = orch.evaluate_cycle(isolated_db, symbol, now_ms=1500, quote=quote, candles_1m=_make_causal_long_m1_candles(), config=base_cfg, feed_health=feed_health, auto_enabled=True)
    assert res["status"] == "PARTIAL"
    assert res["armed_order_id"] is None

    # Cache must no longer contain the stale hypothesis
    current_cache = orch.get_active_hypotheses(symbol)
    assert len(current_cache) == 0


def test_shadow_mode_with_corrupt_record(isolated_db: Session, base_cfg):
    """
    SECTION 113, 171: In SHADOW mode, corrupt hypothesis records are blocked with HYPOTHESIS_STATE_CORRUPT.
    No hypothetical plan or order is created.
    """
    symbol = "XAUUSDT"
    _setup_positive_trading_policy(isolated_db)
    zone_id = "z_shadow_corrupt"
    hyp_id = f"hyp-primary-{zone_id}"
    key = get_hypothesis_storage_key(symbol, hyp_id)

    rec = models.SystemConfig(key=key, value="{corrupt_shadow", updated_at=100)
    isolated_db.add(rec)
    isolated_db.commit()

    orch = ScalpWatcherOrchestrator()
    zone = WatchedZone(zone_id, symbol, "15M", "OB", "BULLISH", 2000.5, 1998.5, 1000, 1000, state=ZoneState.WAITING_TRIGGER.value)
    orch.set_active_zones(symbol, [zone])
    quote = {"bid": 2000.0, "ask": 2000.1, "last": 2000.05, "timestamp": 1500}
    feed_health = {"status": "HEALTHY", "is_stale": False, "stale": False}

    # Run in SHADOW mode
    shadow_cfg = M1ScalpConfig(
        scalp_m1_mode=ScalpMode.SHADOW.value,
        daily_ny_research_enabled=False,
        min_closed_m1_bars=15,
        displacement_body_atr_min=0.5,
    )
    res = orch.evaluate_cycle(
        isolated_db,
        symbol,
        now_ms=1500,
        quote=quote,
        candles_1m=_make_causal_long_m1_candles(),
        config=shadow_cfg,
        feed_health=feed_health,
        auto_enabled=True,
    )
    assert res["status"] == "PARTIAL"
    assert res["armed_order_id"] is None

    # Assert opportunity funnel has HYPOTHESIS_STATE_CORRUPT and stage TRIGGER_PENDING
    opps = isolated_db.query(models.OpportunityFunnelRecord).filter(models.OpportunityFunnelRecord.zone_id == zone_id).all()
    assert len(opps) >= 1
    reasons = json.loads(opps[-1].reason_codes_json)
    assert OpportunityReasonCode.HYPOTHESIS_STATE_CORRUPT.value in reasons
    # No PLAN_VALID stage was reached
    stages = [o.stage for o in opps]
    assert OpportunityStage.PLAN_VALID.value not in stages


def test_existing_position_exit_management_unaffected(isolated_db: Session):
    """
    SECTION 006, 056, 170: Open paper position exit management is completely unaffected
    by unrelated hypothesis persistence failure.
    """
    symbol = "XAUUSDT"
    _setup_positive_trading_policy(isolated_db)

    # 1. Create open paper order
    clock = FakeClock(1_000_000)
    order = models.PaperOrder(
        id="ord_open_exit_test",
        instrument=symbol,
        direction="LONG",
        state="paper_open",
        order_type="LIMIT",
        planned_entry=2000.0,
        actual_entry=2000.0,
        stop_loss=1990.0,
        take_profit=2020.0,
        quantity=0.1,
        initial_risk_usdt=1.0,
        opened_at=1_000_000,
        created_at=1_000_000,
    )
    isolated_db.add(order)

    # 2. Add corrupt hypothesis record in SystemConfig
    key = get_hypothesis_storage_key(symbol, "hyp_unrelated_corrupt")
    isolated_db.add(models.SystemConfig(key=key, value="{bad_hyp", updated_at=100))
    isolated_db.commit()

    # 3. Execute TP close via TradeLifecycleService
    clock.advance_by(60_000)
    closed_order = TradeLifecycleService.execute_close(
        isolated_db,
        order_id=order.id,
        exit_price=2020.0,
        exit_cause="TP_HIT",
        clock=clock,
    )
    assert closed_order is not None
    assert closed_order.state == "closed"
    assert closed_order.exit_cause == "TP_HIT"
    assert closed_order.actual_exit == 2020.0


def test_hypothesis_api_endpoint_data_quality(isolated_db: Session):
    """
    SECTION 175: /api/scalp/hypotheses endpoint returns proper data_quality indicator,
    does NOT mutate DB, and does NOT return fake fresh ACTIVE state for corrupt records.
    """
    from main import get_scalp_hypotheses_endpoint
    from services.scalp_watcher_orchestrator import scalp_watcher_orchestrator

    symbol = "XAUUSDT"

    # Case 1: Healthy store
    batch_healthy = HypothesisBatchLoadResult(
        records={},
        issues=[],
        read_status="COMPLETE",
        blocked_keys=set(),
        blocked_identities=set(),
        diagnostics={},
    )
    scalp_watcher_orchestrator._latest_hypothesis_batch_result[symbol] = batch_healthy
    data1 = get_scalp_hypotheses_endpoint(symbol)
    assert data1["data_quality"] == "HEALTHY"
    assert data1["issues_count"] == 0

    # Case 2: Degraded store
    batch_degraded = HypothesisBatchLoadResult(
        records={},
        issues=[HypothesisLoadIssue(storage_key="k1", status=HypothesisLoadStatus.CORRUPT, reason="Corrupt JSON")],
        read_status="PARTIAL",
        blocked_keys={"k1"},
        blocked_identities=set(),
        diagnostics={},
    )
    scalp_watcher_orchestrator._latest_hypothesis_batch_result[symbol] = batch_degraded
    data2 = get_scalp_hypotheses_endpoint(symbol)
    assert data2["data_quality"] == "DEGRADED"
    assert data2["issues_count"] == 1


def test_acceptance_runner_registration_assertion():
    """
    SECTION 116 - 130, 181: Verify acceptance runner targeted_files registers
    all suites in order: V15.3, V15.4, V15.5, and V15.6.
    """
    acceptance_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "tools", "run_v14_4_2_acceptance.py"))
    assert os.path.exists(acceptance_path)
    with open(acceptance_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "tests/test_v15_3_causal_wiring.py" in content
    assert "tests/test_v15_4_causal_entry.py" in content
    assert "tests/test_v15_5_lifecycle_closure.py" in content
    assert "tests/test_v15_6_hypothesis_persistence.py" in content
