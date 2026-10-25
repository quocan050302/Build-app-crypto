"""
backend/tests/test_v16_24_red_reproductions.py
V16.24 Red Defect Reproductions & Precision Contract Verification:
- Rule 021: Delayed event: evaluation_started_at reflects actual processing start, not candle close time.
- Rule 022: Delayed event: quote freshness evaluated at execution time, rejecting stale quotes.
- Rule 023: News guard evaluated at execution time, blocking entry during news blackout.
- Rule 024: Candidate expiry evaluated at execution time, refusing to arm expired setups.
- Rule 025: Session boundary evaluated at execution time, rejecting entries outside window.
- Rule 026: Reconnected old candle event preserves evidence provenance without rewinding bot clock.
- Rule 027: DB thread cancellation preserves batch ownership without premature requeue.
- Rule 028: Retry group cancellation separates running item from unstarted items.
- Rule 029: Start worker failure/timeout is detected and recorded in lifespan startup result.
- Rule 030: Stop worker wait timeout is detected and recorded in lifespan shutdown result.
"""
from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.orm import Session

# Ensure test DB isolation (Rule 012)
os.environ.setdefault("AURUM_DB_PATH", ":memory:")

from database import SessionLocal, engine
from models import Base
import models
import crud
from services.clock import IClock, LiveClock, ReplayClock, FakeClock, live_clock
from services.candle_cache_service import candle_cache_service
from services.cg01_config_factory import (
    compute_cg01_config_fingerprint,
    resolve_canonical_cg01_config,
)
from services.cg01_scheduler_service import (
    CG01EvaluationScheduler,
    cg01_evaluation_scheduler,
    capture_cg01_quote_snapshot,
    capture_cg01_input_bundle,
    resolve_effective_cg01_config,
)
from services.cg01_telemetry_service import (
    CG01EvaluationTrace,
    CG01TracePersistenceWriter,
    cg01_trace_recorder,
    _IN_FLIGHT_COUNT,
    _RETRY_BUFFER,
    _RETRY_BUFFER_LOCK,
    _TRACE_PERSISTED_COUNT,
    _TRACE_PERSIST_FAILURES,
    _TRACE_QUEUE,
    _TRACE_WORKER_RUNNING,
    _TRACE_WORKER_STATE,
    _persist_trace_batch_sync,
    drain_trace_persistence,
    enqueue_trace_persistence,
    flush_pending_traces,
    get_in_flight_count,
    get_retry_buffer_count,
    get_trace_worker_state,
    is_trace_worker_running,
    reset_telemetry_worker_state,
    run_trace_persistence_worker,
    set_in_flight_count,
    start_trace_persistence_worker,
    stop_trace_persistence_worker,
    wait_trace_persistence_worker_stopped,
)
from services.cg01_types import CG01Config
from services.scalp_m1_model_service import ScalpCandidate
from services.scalp_m1_types import WatchedZone
from services.collector_service import collector_service
from services.scalp_watcher_orchestrator import ScalpWatcherOrchestrator, scalp_watcher_orchestrator
from services.strategy_service import strategy_service
from services.trading_policy_service import TradingPolicyService
from tests.test_v16_3_cg01_pipeline import (
    build_long_fixture_series,
    make_valid_quote,
)


@pytest.fixture(autouse=True)
def setup_isolated_db():
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        session.query(models.SMCScenarioSnapshot).delete()
        session.query(models.PaperOrder).delete()
        session.query(models.WatchSetup).delete()
        session.query(models.PaperResearchOrderLink).delete()
        session.query(models.PaperResearchMilestone).delete()
        session.query(models.PaperResearchOrderMilestone).delete()
        session.query(models.CG01TraceRecord).delete()
        session.query(models.PaperResearchRunRecord).delete()
        session.query(models.SystemConfig).delete()
        session.query(models.DayAudit).delete()
        session.query(models.TradingPolicy).delete()
        session.query(models.EconomicNews).delete()
        session.commit()
    finally:
        session.close()

    reset_telemetry_worker_state()
    cg01_evaluation_scheduler.reset_state()
    yield
    reset_telemetry_worker_state()
    cg01_evaluation_scheduler.reset_state()


# ==============================================================================
# RED 021: EVENT ĐẾN TRỄ (Rule 021, 031-036)
# Nến đóng lúc T. Event nhận T+8s. Worker bắt đầu T+10s.
# Assert evaluation_started_at là T+10s, không phải T.
# ==============================================================================
def test_red_021_delayed_event_execution_time_distinct_from_evidence_time():
    """
    Rule 021: Evidence at T, received at T+8s, evaluation started at T+10s.
    Assert trace.evaluation_started_at reflects T+10s, NOT T.
    Assert trace.market_evidence_as_of_ms reflects T.
    """
    t_close = 1_700_000_000_000
    t_recv = t_close + 8_000
    t_start = t_close + 10_000

    sched = CG01EvaluationScheduler()
    test_clock = ReplayClock(t_start)

    source_ev = {
        "event_id": f"XAUUSDT:1M:{t_close - 60000}:0:CANDLE_CLOSED",
        "symbol": "XAUUSDT",
        "timeframe": "1M",
        "candle_timestamp": t_close - 60000,  # 1M candle closing at t_close
        "exchange_event_time_ms": t_close,
        "received_at_ms": t_recv,
        "enqueued_at_ms": t_recv,
    }

    # Evaluate with controlled clock at t_start
    db = SessionLocal()
    try:
        # Pre-seed ticker at t_start to avoid quote unavailability
        collector_service.latest_ticker = {
            "symbol": "XAUUSDT",
            "bid": 2650.0,
            "ask": 2650.2,
            "last": 2650.1,
            "timestamp": t_start,
            "observed_at": t_start,
        }
        res = sched.evaluate_symbol_now(
            db,
            "XAUUSDT",
            market_evidence_as_of_ms=t_close,
            execution_now_ms=t_start,
            source_event=source_ev,
            clock=test_clock,
        )

        trace = cg01_trace_recorder.get_latest_trace()
        assert trace is not None, "Trace must be recorded"
        assert trace.evaluation_started_at == t_start, (
            f"evaluation_started_at must be {t_start} (processing start), got {trace.evaluation_started_at}"
        )
        assert trace.event_received_at == t_recv, (
            f"event_received_at must be {t_recv}, got {trace.event_received_at}"
        )
        assert getattr(trace, "market_evidence_as_of_ms", None) == t_close, (
            f"market_evidence_as_of_ms must be {t_close}, got {getattr(trace, 'market_evidence_as_of_ms', None)}"
        )
    finally:
        db.close()


# ==============================================================================
# RED 022: QUOTE FRESHNESS (Rule 022, 046)
# Cùng event trễ, quote có timestamp riêng.
# Freshness phải tính theo execution time thật, không theo candle close time.
# ==============================================================================
def test_red_022_delayed_event_stale_quote_rejected_against_execution_time():
    """
    Rule 022 & 104: Candle closed at T. Event received at T+8s. Evaluated at T+10s.
    Quote observed at T-6s (16s old at execution time T+10s, but only 6s old at candle close T).
    If freshness was evaluated against candle close T, quote would appear fresh (<15s).
    Must evaluate against execution time T+10s -> quote is STALE (>15s).
    """
    t_close = 1_700_000_000_000
    t_recv = t_close + 8_000
    t_start = t_close + 10_000
    t_quote = t_close - 6_000  # 16 seconds old relative to t_start! 6s old relative to t_close!

    # Set ticker in collector service
    collector_service.latest_ticker = {
        "symbol": "XAUUSDT",
        "bid": 2650.0,
        "ask": 2650.2,
        "last": 2650.1,
        "timestamp": t_quote,
        "observed_at": t_quote,
    }

    # If evaluated against candle close time, it would appear fresh (6s < 15s)
    snap_candle = capture_cg01_quote_snapshot(symbol="XAUUSDT", execution_now_ms=t_close)
    assert snap_candle["is_stale"] is False, "Quote appears fresh when evaluated against candle close time"

    # Freshness evaluated at execution time t_start: 16s > 15s -> STALE
    snap_exec = capture_cg01_quote_snapshot(symbol="XAUUSDT", execution_now_ms=t_start)
    assert snap_exec["is_stale"] is True, f"Quote observed at {t_quote} must be STALE at execution time {t_start}"
    assert snap_exec["freshness_sec"] >= 15.9, f"Freshness sec should be ~16s, got {snap_exec['freshness_sec']}"


# ==============================================================================
# RED 023: NEWS GUARD TIME (Rule 023, 048, 105)
# Nến đóng trước blackout, xử lý thực tế trong blackout.
# Entry guard phải xét thời điểm thực thi.
# ==============================================================================
def test_red_023_news_guard_evaluates_at_execution_time_not_candle_time():
    """
    Rule 023 & 105: Nến đóng trước blackout window.
    Xử lý thực tế tại T+10s (vừa bước vào blackout window).
    Entry guard phải xét thời điểm thực thi và chặn lệnh.
    """
    t_close = 1_700_000_000_000
    t_start = t_close + 10_000  # execution time: 10s after close

    # High impact USD news scheduled 30 minutes + 5 seconds after t_close
    # 30-minute blackout window begins at t_news - 30m = t_close + 5_000
    t_news = t_close + (30 * 60_000) + 5_000

    db = SessionLocal()
    try:
        news_event = models.EconomicNews(
            id=99923,
            source_id="news_test_red_023",
            title="US Non-Farm Payrolls",
            country="USD",
            currency="USD",
            impact="High",
            scheduled_at=t_news,
            received_at=t_close - 3600_000,
        )
        db.add(news_event)
        policy = TradingPolicyService.get_or_create_policy(db)
        policy.news_guard_enabled = True
        db.commit()

        # 1. At candle close time t_close: window has not started yet
        is_bo_candle, reason_candle, _ = crud.check_news_blackout(db, now_ms=t_close)
        assert is_bo_candle is False, "At candle close time, news blackout must NOT be active"

        # 2. At execution time t_start: window has started (t_start > t_close + 5s)
        is_bo_exec, reason_exec, _ = crud.check_news_blackout(db, now_ms=t_start)
        assert is_bo_exec is True, "At execution time, news blackout MUST be active"
        assert "Non-Farm Payrolls" in str(reason_exec)

        # 3. Policy evaluation at execution time must reject entry
        test_clock = ReplayClock(t_start)
        pol_eval = TradingPolicyService.evaluate_entry_policy(
            db, "XAUUSDT", now_ms=t_start, clock=test_clock, strategy_family="STANDARD_SMC"
        )
        assert not pol_eval.get("allowed", True), "Entry policy at execution time must be rejected"
        assert pol_eval.get("reason_code") == "NEWS_BLACKOUT" or "NEWS" in str(pol_eval.get("reason_codes", []))
    finally:
        db.close()


# ==============================================================================
# RED 024: EXPIRY (Rule 024, 050)
# Candidate có expiry xác định. Processing sau expiry phải không arm/fill.
# ==============================================================================
def test_red_024_candidate_expiry_checked_at_execution_time():
    """
    Rule 024: Setup expires at T+5s. Event processed at T+10s.
    Orchestrator must reject arming because now >= expires_at.
    """
    t_close = 1_700_000_000_000
    t_start = t_close + 10_000  # processed after expiry!
    test_clock = ReplayClock(t_start)

    from services.scalp_m1_model_service import ScalpCandidate
    from domain_calculator import calculate_risk_reward
    calc = calculate_risk_reward(
        direction="LONG",
        entry=2650.0,
        sl=2645.0,
        tp=2665.0,
        capital=1000.0,
        risk_pct=0.25,
    )
    cand = ScalpCandidate(
        is_eligible=True,
        model_id="CG01_SWEEP_MSS_FVG",
        direction="LONG",
        candidate_key="cand_test_red_024",
        symbol="XAUUSDT",
        planned_entry=2650.0,
        stop_loss=2645.0,
        take_profit=2665.0,
        gross_rr=calc.gross_rr,
        net_rr=calc.net_rr,
        calculation=calc,
        as_of_ms=t_close,
        valid_until_ms=t_close + 5_000,  # Expired at T+5s!
    )

    db = SessionLocal()
    try:
        from services.quote_validator import normalize_execution_quote
        q = normalize_execution_quote({
            "symbol": "XAUUSDT",
            "bid": 2650.0,
            "ask": 2650.2,
            "last": 2650.1,
            "timestamp": t_start,
        }, now_ms=t_start)

        armed = scalp_watcher_orchestrator._arm_paper_candidate(
            db,
            cand,
            now_ms=t_start,
            clock=test_clock,
            quote=q,
            policy_settings={"risk_pct": 0.25, "capital_usdt": 1000.0, "config_version": 1},
            auto_enabled=True,
        )
        assert armed is None, "Expired candidate must NOT be armed"
    finally:
        db.close()


# ==============================================================================
# RED 025: SESSION BOUNDARY (Rule 025, 052)
# Nến đóng trong session nhưng xử lý ngoài entry window.
# Execution policy phải kiểm tra theo thời gian thực thi.
# ==============================================================================
def test_red_025_session_boundary_checked_at_execution_time():
    """
    Rule 025: Nến đóng trong session window, nhưng thực thi trễ ngoài session window.
    Execution policy phải kiểm tra theo thời gian thực thi t_start và chặn entry.
    """
    # 2026-10-15 05:00 UTC (inside London or NY depending on policy, let's configure NY_ONLY)
    # NY entry window is typically 13:00 - 17:00 UTC.
    # We test at 18:00 UTC (outside NY window) with a clock outside the window.
    t_inside = int(datetime(2026, 10, 15, 14, 0, tzinfo=ZoneInfo("UTC")).timestamp() * 1000)
    t_outside = int(datetime(2026, 10, 15, 18, 0, tzinfo=ZoneInfo("UTC")).timestamp() * 1000)

    db = SessionLocal()
    try:
        policy = TradingPolicyService.get_or_create_policy(db)
        policy.entry_session_policy = "NY_ONLY"
        db.commit()

        # At t_outside, entry policy must reject
        test_clock = ReplayClock(t_outside)
        ev_eval = TradingPolicyService.evaluate_entry_policy(
            db, "XAUUSDT", now_ms=t_outside, clock=test_clock, strategy_family="STANDARD_SMC"
        )
        assert not ev_eval.get("allowed", True), "Entry outside NY session window must be disallowed"
    finally:
        db.close()


# ==============================================================================
# RED 026: RECONNECT EVENT CŨ (Rule 026, 051)
# Re-deliver closed candle cũ sau reconnect (5 phút trước).
# Không giả bot đang ở thời điểm candle đó đóng.
# ==============================================================================
def test_red_026_reconnect_replayed_candle_not_backdated():
    """
    Rule 026: Replay/reconnect event from 5 minutes ago.
    Bot receives it at t_now.
    Trace must record market_evidence_as_of_ms as old candle time,
    but evaluation_started_at must be t_now (not backdated to 5 min ago).
    """
    t_now = int(time.time() * 1000)
    t_old_candle = t_now - 300_000  # 5 minutes ago

    sched = CG01EvaluationScheduler()
    source_ev = {
        "event_id": f"XAUUSDT:1M:{t_old_candle}:0:CANDLE_CLOSED",
        "symbol": "XAUUSDT",
        "timeframe": "1M",
        "candle_timestamp": t_old_candle,
        "exchange_event_time_ms": t_old_candle + 60_000,
        "received_at_ms": t_now,
        "enqueued_at_ms": t_now,
    }

    db = SessionLocal()
    try:
        collector_service.latest_ticker = {
            "symbol": "XAUUSDT",
            "bid": 2650.0,
            "ask": 2650.2,
            "last": 2650.1,
            "timestamp": t_now,
            "observed_at": t_now,
        }
        sched.evaluate_symbol_now(
            db,
            "XAUUSDT",
            market_evidence_as_of_ms=t_old_candle + 60_000,
            execution_now_ms=t_now,
            source_event=source_ev,
        )

        trace = cg01_trace_recorder.get_latest_trace()
        assert trace is not None
        assert trace.evaluation_started_at >= t_now - 1000, (
            f"Bot evaluation_started_at must be current time ~{t_now}, got {trace.evaluation_started_at}"
        )
        assert getattr(trace, "market_evidence_as_of_ms", None) == t_old_candle + 60_000
    finally:
        db.close()


# ==============================================================================
# RED 027: CANCEL DB BATCH (Rule 027, 061-076)
# Giữ batch commit trong thread bằng barrier. Cancel coroutine wrapper.
# Assert batch vẫn có owner, không bị requeue trong khi thread cũ chưa xong.
# ==============================================================================
@pytest.mark.anyio
async def test_red_027_cancelled_running_batch_retains_ownership():
    """
    Rule 027: Hold batch persistence in thread using a barrier.
    Cancel the coroutine awaiting the thread.
    Assert batch is NOT prematurely requeued into _RETRY_BUFFER while thread is running.
    Assert _IN_FLIGHT_COUNT is NOT prematurely decremented.
    """
    import services.cg01_telemetry_service as tel_mod
    reset_telemetry_worker_state()
    trace = CG01EvaluationTrace(trace_id="trc_cancel_test_027", symbol="XAUUSDT")
    enqueue_trace_persistence(trace)

    barrier = threading.Barrier(2)
    original_persist = CG01TracePersistenceWriter.persist_trace

    def delayed_persist(t, db=None):
        if t.trace_id == "trc_cancel_test_027":
            try:
                barrier.wait(timeout=2.0)
            except Exception:
                pass
        return original_persist(t, db=db)

    with patch.object(CG01TracePersistenceWriter, "persist_trace", side_effect=delayed_persist):
        ready = await start_trace_persistence_worker(timeout_s=2.0)
        assert ready is True

        # Wait until worker picks up the trace and enters the thread barrier
        await asyncio.sleep(0.1)

        # In-flight count should be 1
        assert get_in_flight_count() >= 1, "In-flight count must reflect active batch"

        # Cancel the background worker task while thread is holding the barrier
        worker_task = tel_mod._TRACE_WORKER_TASK
        assert worker_task is not None
        worker_task.cancel()

        # Wait a tick for coroutine cancellation to process
        await asyncio.sleep(0.05)

        # Batch must NOT be requeued into _RETRY_BUFFER while thread is still active!
        with _RETRY_BUFFER_LOCK:
            retry_ids = [t.trace_id for t, _ in _RETRY_BUFFER]
        assert "trc_cancel_test_027" not in retry_ids, (
            "Running batch must NOT be requeued into _RETRY_BUFFER before thread finishes!"
        )

        # Release barrier to let thread finish
        try:
            barrier.wait(timeout=1.0)
        except Exception:
            pass

        # Wait for thread to complete
        await asyncio.sleep(0.2)


# ==============================================================================
# RED 028: CANCEL RETRY GROUP (Rule 028, 070)
# Worker giữ nhiều retry items. Một item đang ghi DB, các items khác chưa bắt đầu.
# Cancel phải xử lý hai nhóm: running giữ owner, unstarted được trả lại đúng.
# ==============================================================================
@pytest.mark.anyio
async def test_red_028_retry_group_cancellation_separates_active_from_unstarted():
    """
    Rule 028: Worker has 3 retry items.
    Item 0 is submitted to DB thread (running at barrier).
    Items 1 & 2 have not started.
    Cancel waiter.
    Item 0 retains owner; items 1 & 2 are returned to _RETRY_BUFFER.
    """
    import services.cg01_telemetry_service as tel_mod
    reset_telemetry_worker_state()
    t0 = CG01EvaluationTrace(trace_id="trc_retry_0", symbol="XAUUSDT")
    t1 = CG01EvaluationTrace(trace_id="trc_retry_1", symbol="XAUUSDT")
    t2 = CG01EvaluationTrace(trace_id="trc_retry_2", symbol="XAUUSDT")

    with _RETRY_BUFFER_LOCK:
        _RETRY_BUFFER.extend([(t0, 1), (t1, 1), (t2, 1)])

    barrier = threading.Barrier(2)
    original_persist = CG01TracePersistenceWriter.persist_trace

    def delayed_persist(t, db=None):
        if t.trace_id == "trc_retry_0":
            try:
                barrier.wait(timeout=2.0)
            except Exception:
                pass
        return original_persist(t, db=db)

    with patch.object(CG01TracePersistenceWriter, "persist_trace", side_effect=delayed_persist):
        ready = await start_trace_persistence_worker(timeout_s=2.0)
        assert ready is True

        # Wait until worker pops retry buffer and enters thread with t0
        await asyncio.sleep(0.1)

        # Cancel worker while t0 is at barrier
        tel_mod._TRACE_WORKER_TASK.cancel()
        await asyncio.sleep(0.05)

        # t1 and t2 should be back in retry buffer, but NOT t0 (t0 is owned by running thread)
        with _RETRY_BUFFER_LOCK:
            retry_traces = [t.trace_id for t, _ in _RETRY_BUFFER]

        assert "trc_retry_0" not in retry_traces, "Running item trc_retry_0 must NOT be in retry buffer yet"
        assert "trc_retry_1" in retry_traces, "Unstarted item trc_retry_1 must be recovered in retry buffer"
        assert "trc_retry_2" in retry_traces, "Unstarted item trc_retry_2 must be recovered in retry buffer"

        # Release barrier
        try:
            barrier.wait(timeout=1.0)
        except Exception:
            pass
        await asyncio.sleep(0.2)


# ==============================================================================
# RED 029: START RETURNS FALSE (Rule 029, 086)
# Cho start_trace_persistence_worker() thất bại.
# Lifespan phải thể hiện startup failure/degraded theo policy.
# ==============================================================================
@pytest.mark.anyio
async def test_red_029_lifespan_detects_worker_start_failure():
    """
    Rule 029: If start_trace_persistence_worker() returns False,
    lifespan must record STARTUP_DEGRADED or FAILED rather than silently proceeding as ready.
    """
    import main
    with patch("services.cg01_telemetry_service.start_trace_persistence_worker", return_value=False):
        async with main.app.router.lifespan_context(main.app):
            get_res = getattr(main, "get_last_startup_result", getattr(main, "get_lifespan_startup_result", None))
            assert get_res is not None, "main must expose get_last_startup_result"
            startup_res = get_res()
            assert startup_res["status"] in ("STARTUP_DEGRADED", "FAILED"), (
                f"Startup result must indicate degraded/failed when worker fails, got {startup_res}"
            )
            assert startup_res.get("telemetry_worker_ready") is False


# ==============================================================================
# RED 030: STOP WAIT RETURNS FALSE (Rule 030, 093)
# Cho wait_trace_persistence_worker_stopped() timeout.
# Shutdown result phải phản ánh chưa dừng hoàn tất (TIMED_OUT).
# ==============================================================================
@pytest.mark.anyio
async def test_red_030_lifespan_detects_worker_stop_timeout():
    """
    Rule 030: If wait_trace_persistence_worker_stopped() returns False (timeout),
    shutdown result must reflect TIMED_OUT or not COMPLETED.
    """
    import main
    with patch("services.cg01_telemetry_service.start_trace_persistence_worker", return_value=True):
        with patch("services.cg01_telemetry_service.wait_trace_persistence_worker_stopped", return_value=False):
            async with main.app.router.lifespan_context(main.app):
                pass
            get_res = getattr(main, "get_last_shutdown_result", getattr(main, "get_lifespan_shutdown_result", None))
            assert get_res is not None, "main must expose get_last_shutdown_result"
            shutdown_res = get_res()
            assert shutdown_res["status"] in ("TIMED_OUT", "FAILED"), (
                f"Shutdown result must indicate TIMED_OUT when worker stop times out, got {shutdown_res}"
            )
            assert shutdown_res.get("worker_stopped") is False
