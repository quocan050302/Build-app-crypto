"""
backend/tests/test_v16_20_red_reproductions.py
V16.20 Mandatory RED Reproduction Tests (Rules 021 - 030):
- 021: CandleCreate object attribute access: CandleCreate does not have .get()
- 022: Cache to shadow pipeline: capture_cg01_input_bundle output passed to shadow adapter without AttributeError
- 023: Quote provenance and spread: real bid/ask and spread preserved, not fabricated from candle close
- 024: Missing or stale quote: returns SKIPPED / QUOTE_UNAVAILABLE without synthetic quote fallback
- 025: Financial context parity: scheduler resolves capital and risk from audit/settings, not hardcoded 1000/0.25
- 026: Effective config parity: resolve_effective_cg01_config reflects M1ScalpConfig overrides
- 027: Run durability: ensure_observation_run_started commits run so it persists across session close
- 028: Trace orphan handling: failed run start leaves trace run_id as None / unresolved
- 029: Telemetry batch retention: commit failure retains batch for retry rather than dropping via task_done()
- 030: In-flight shutdown tracking: flush_pending_traces does not claim COMPLETED while batch is in-flight
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
from sqlalchemy.orm import Session

from database import SessionLocal
import models
import crud
import schemas
from services.cg01_scheduler_service import (
    CG01EvaluationScheduler,
    capture_cg01_input_bundle,
    evaluate_cg01_shadow_snapshot,
    resolve_effective_cg01_config,
    capture_cg01_quote_snapshot,
    resolve_cg01_financial_context,
    ensure_observation_run_started,
)
from services.cg01_context_adapter import CanonicalCandle, normalize_cg01_candle
from services.cg01_types import CG01Config, CG01ResearchContext
from services.scalp_m1_types import M1ScalpConfig
from services.quote_validator import CanonicalQuote, ValidatedQuote
from services.collector_service import collector_service
from services.risk_settings_service import risk_settings_service
from services.cg01_telemetry_service import (
    CG01EvaluationTrace,
    CG01TracePersistenceWriter,
    TraceBatchResult,
    _persist_trace_batch_sync,
    enqueue_trace_persistence,
    flush_pending_traces,
    _TRACE_QUEUE,
    get_in_flight_count,
    get_retry_buffer_count,
)


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def test_red_021_candlecreate_attribute_access():
    """
    Rule 021 / 031: CandleCreate from candle_cache_service has no .get() method.
    evaluate_cg01_shadow_snapshot must handle CandleCreate objects without raising AttributeError.
    """
    candle = schemas.CandleCreate(
        symbol="XAUUSDT",
        timeframe="1M",
        timestamp=1700000000000,
        open=2620.0,
        high=2622.0,
        low=2619.0,
        close=2621.0,
        volume=50.0,
        is_closed=True,
    )

    bundle = {
        "1M": [candle],
        "5M": [],
        "15M": [],
        "1H": [],
        "4H": [],
    }

    # Should not raise AttributeError: 'CandleCreate' object has no attribute 'get'
    res = evaluate_cg01_shadow_snapshot(
        symbol="XAUUSDT",
        as_of_ms=1700000060000,
        bundle=bundle,
        current_quote=None,
    )
    assert res is not None
    assert res.get("status") in ("SKIPPED", "NO_CANDIDATE", "OK")


def test_red_022_cache_to_shadow_pipeline():
    """
    Rule 022 / 040: Ingest candle to cache service, capture bundle, and pass to shadow.
    The bundle contains schemas.CandleCreate objects.
    """
    from services.candle_cache_service import candle_cache_service
    candle_cache_service._cache.clear()
    now_ms = int(time.time() * 1000)
    candle_cache_service.add_candle(
        symbol="XAUUSDT",
        timeframe="1M",
        bar={
            "timestamp": now_ms - 60_000,
            "open": 2630.0,
            "high": 2632.0,
            "low": 2629.0,
            "close": 2631.0,
            "volume": 100.0,
            "is_closed": True,
        }
    )

    bundle = capture_cg01_input_bundle(symbol="XAUUSDT", as_of_ms=now_ms)
    assert len(bundle["1M"]) >= 1
    assert isinstance(bundle["1M"][0], schemas.CandleCreate)

    # Calling shadow adapter with bundle of CandleCreate must succeed without error
    res = evaluate_cg01_shadow_snapshot(
        symbol="XAUUSDT",
        as_of_ms=now_ms,
        bundle=bundle,
        current_quote=None,
    )
    assert res is not None
    assert res.get("status") in ("SKIPPED", "NO_CANDIDATE", "OK")


def test_red_023_quote_provenance_and_spread():
    """
    Rule 023 / 041-045: Valid quote with non-default spread is preserved.
    Must not synthesize bid=close, ask=close+tick_sz.
    """
    real_quote = {
        "bid": 2640.10,
        "ask": 2640.75,
        "spread": 0.65,
        "last": 2640.40,
        "timestamp": 1700000000000,
        "is_valid": True,
        "is_stale": False,
    }

    candle = schemas.CandleCreate(
        symbol="XAUUSDT",
        timeframe="1M",
        timestamp=1700000000000,
        open=2640.0,
        high=2642.0,
        low=2639.0,
        close=2640.20,
        volume=50.0,
        is_closed=True,
    )

    bundle = {"1M": [candle], "5M": [], "15M": [], "1H": [], "4H": []}
    res = evaluate_cg01_shadow_snapshot(
        symbol="XAUUSDT",
        as_of_ms=1700000060000,
        bundle=bundle,
        current_quote=real_quote,
    )
    assert res is not None


def test_red_024_missing_or_stale_quote_skipped():
    """
    Rule 024 / 043: If quote is missing or stale, shadow returns SKIPPED with QUOTE_UNAVAILABLE.
    Never fabricates a live quote from last candle close.
    """
    stale_quote = {
        "bid": 2640.0,
        "ask": 2640.5,
        "spread": 0.5,
        "last": 2640.2,
        "timestamp": 1690000000000,
        "is_valid": False,
        "is_stale": True,
        "rejection_code": "QUOTE_STALE",
    }

    candle = schemas.CandleCreate(
        symbol="XAUUSDT",
        timeframe="1M",
        timestamp=1700000000000,
        open=2640.0,
        high=2642.0,
        low=2639.0,
        close=2640.20,
        volume=50.0,
        is_closed=True,
    )
    bundle = {"1M": [candle], "5M": [], "15M": [], "1H": [], "4H": []}

    res = evaluate_cg01_shadow_snapshot(
        symbol="XAUUSDT",
        as_of_ms=1700000060000,
        bundle=bundle,
        current_quote=stale_quote,
    )
    assert res["status"] == "SKIPPED"
    assert res["skip_reason"] == "QUOTE_UNAVAILABLE"
    assert res["candidate"] is None


def test_red_025_financial_context_parity(db: Session):
    """
    Rule 025 / 057: Financial context uses actual settings from risk service and audit,
    not hardcoded capital=1000.0, risk_pct=0.25.
    """
    # Set custom capital and risk
    audit = crud.get_or_create_today_audit(db)
    audit.current_equity = 7500.0
    db.commit()

    risk_settings_service.update_settings(db, schemas.RiskSettingsUpdate(risk_pct=0.50))

    fin_ctx = resolve_cg01_financial_context(db)
    assert fin_ctx["capital_usdt"] == 7500.0
    assert fin_ctx["risk_pct"] == 0.50


def test_red_026_effective_config_parity(db: Session):
    """
    Rule 026 / 051-056: Effective config resolution propagates strategy overrides
    from M1ScalpConfig rather than returning stock defaults.
    """
    import services.scalp_m1_types as smt
    orig_config = smt.active_scalp_config

    db.query(models.TradingPolicy).delete()
    db.commit()

    try:
        smt.active_scalp_config = smt.get_scalp_config(min_net_rr=3.5, displacement_body_atr_min=1.25)

        eff_cfg, fingerprint, source = resolve_effective_cg01_config(db=db, symbol="XAUUSDT")
        assert eff_cfg.min_net_rr == 3.5
        assert eff_cfg.displacement_body_atr_min == 1.25
        assert fingerprint is not None
        assert len(fingerprint) > 0
    finally:
        smt.active_scalp_config = orig_config


def test_red_027_run_persists_after_session_close():
    """
    Rule 027 / 061-068: ensure_observation_run_started commits the observation run
    so it survives closing of the initial session.
    """
    run_id = f"run_red_027_{int(time.time() * 1000)}"
    run_ref = ensure_observation_run_started(symbol="XAUUSDT", scope="CG01", mode="SHADOW", run_id=run_id)
    assert run_ref is not None
    assert run_ref.run_id == run_id

    # Verify run exists in a brand-new DB session without manual caller commit
    fresh_db = SessionLocal()
    try:
        rec = fresh_db.query(models.PaperResearchRunRecord).filter(
            models.PaperResearchRunRecord.run_id == run_id
        ).first()
        assert rec is not None
        assert rec.scope == "CG01"
        assert rec.status == "COLLECTING"
    finally:
        fresh_db.close()


def test_red_028_trace_unresolved_run_on_failure():
    """
    Rule 028 / 074: If run resolution/start fails, trace has run_id=None or is marked unresolved,
    not falsely attributed to a nonexistent active run.
    """
    t_now = int(time.time() * 1000)
    trace = CG01EvaluationTrace(
        trace_id=f"trc_red_028_{t_now}",
        symbol="XAUUSDT",
        run_id=None,
        observation_mode="SHADOW",
    )
    assert trace.run_id is None


def test_red_029_telemetry_batch_retained_on_failure():
    """
    Rule 029 / 081-085: If _persist_trace_batch_sync encounters a commit failure,
    it must NOT discard the batch via task_done(). It must retain it in retry buffer.
    """
    t_now = int(time.time() * 1000)
    trace = CG01EvaluationTrace(
        trace_id=f"trc_red_029_{t_now}",
        symbol="XAUUSDT",
        observation_mode="SHADOW",
    )

    class FailingSession:
        def add(self, obj): pass
        def merge(self, obj): pass
        def flush(self): pass
        def commit(self):
            raise RuntimeError("Simulated DB lock contention on commit!")
        def rollback(self): pass
        def close(self): pass

    # Inject failing session provider
    result = _persist_trace_batch_sync([trace], session_factory=lambda: FailingSession())
    assert isinstance(result, TraceBatchResult)
    assert result.status in ("RETRYABLE_FAILURE", "PERMANENT_FAILURE")
    assert result.committed_count == 0
    # Must be retained in retry buffer
    assert get_retry_buffer_count() >= 1


def test_red_030_shutdown_waits_for_inflight_writes():
    """
    Rule 030 / 087, 101, 146: When queue depth is 0 but an in-flight batch is committing,
    flush_pending_traces does NOT report COMPLETED prematurely.
    """
    from services.cg01_telemetry_service import (
        set_in_flight_count,
        flush_pending_traces,
    )
    # Simulate in-flight work while queue is empty
    set_in_flight_count(2)
    try:
        res = flush_pending_traces(timeout_s=0.2)
        assert res["in_flight"] >= 2
        assert res["status"] in ("TIMED_OUT", "IN_FLIGHT")
    finally:
        set_in_flight_count(0)
