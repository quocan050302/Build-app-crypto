"""
backend/tests/test_v16_26_terminal_loss_accounting.py
V16.26 Terminal Telemetry Loss Accounting and Drain Status Reconciliation Suite.

Enforces Rules 021-056, 061-085, 091-130:
- Distinct accounting for _TRACE_QUEUE_DROPS, _TRACE_PERSIST_FAILURES, and _TRACE_TERMINAL_FAILURES.
- terminal_loss = queue_drops + terminal_failures.
- finalize_queue_receipt_once atomic check-and-increment on TERMINAL_FAILURE.
- drain_trace_persistence and flush_pending_traces report COMPLETED_WITH_LOSS when terminal_loss > 0.
- Exactly-once queue task acknowledgement and clean queue.join() completion.
- Transient retries that eventually succeed do not increment terminal loss and report COMPLETED.
"""
from __future__ import annotations

import asyncio
import os
import queue
import threading
import time
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import Session

# Rule 024: Ensure test DB isolation
os.environ.setdefault("AURUM_DB_PATH", ":memory:")

from database import SessionLocal, engine
from models import Base
import models
import services.cg01_telemetry_service as tel_mod
from services.cg01_telemetry_service import (
    CG01EvaluationTrace,
    CG01TracePersistenceWriter,
    QueueReceipt,
    RetryItem,
    _ACTIVE_RECEIPTS,
    _RETRY_BUFFER,
    _RETRY_BUFFER_LOCK,
    _TRACE_PERSIST_FAILURES,
    _TRACE_QUEUE,
    _TRACE_QUEUE_DROPS,
    _persist_trace_batch_sync,
    drain_trace_persistence,
    enqueue_trace_persistence,
    finalize_queue_receipt_once,
    flush_pending_traces,
    get_active_batch_count,
    get_active_operations_count,
    get_in_flight_count,
    get_outstanding_receipt_count,
    get_persist_failures,
    get_persisted_count,
    get_retry_buffer_count,
    reset_telemetry_worker_state,
    start_trace_persistence_worker,
    stop_trace_persistence_worker,
    wait_trace_persistence_worker_stopped,
)


@pytest.fixture(autouse=True)
def setup_isolated_db():
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    reset_telemetry_worker_state()
    try:
        session.query(models.CG01TraceRecord).delete()
        session.commit()
        yield session
    finally:
        reset_telemetry_worker_state()
        session.close()


def _get_terminal_failures() -> int:
    fn = getattr(tel_mod, "get_terminal_failure_count", None)
    if fn:
        return fn()
    return getattr(tel_mod, "_TRACE_TERMINAL_FAILURES", 0)


def _get_queue_drops() -> int:
    fn = getattr(tel_mod, "get_queue_drops", None)
    if fn:
        return fn()
    return getattr(tel_mod, "_TRACE_QUEUE_DROPS", 0)


# ==============================================================================
# TEST 1: SINGLE ITEM TERMINAL EXHAUSTION (Rules 021-028, 094-098)
# ==============================================================================
@pytest.mark.anyio
async def test_red_001_single_item_terminal_exhaustion_recorded_as_completed_with_loss(setup_isolated_db: Session):
    """
    Rules 021, 022, 094-098, 191:
    Enqueue a trace via queue API. Force all attempts to fail.
    Verify:
    - Trace not committed in DB.
    - Queue task ACKed once (unfinished_tasks == 0, queue.join() succeeds).
    - terminal_failures == 1, terminal_loss == 1.
    - drain returns COMPLETED_WITH_LOSS (NOT COMPLETED!).
    """
    ready = await start_trace_persistence_worker(timeout_s=2.0)
    assert ready is True

    t_id = "trc_term_001"
    trace = CG01EvaluationTrace(trace_id=t_id, symbol="XAUUSDT")

    # Fail all persist attempts
    with patch.object(CG01TracePersistenceWriter, "persist_trace", return_value=False):
        enqueued = enqueue_trace_persistence(trace)
        assert enqueued is True

        drain_res = await drain_trace_persistence(timeout_s=4.0)

    # 1. Assert drain status and terminal loss
    assert drain_res["status"] == "COMPLETED_WITH_LOSS", (
        f"Expected COMPLETED_WITH_LOSS after terminal failure, got {drain_res['status']}"
    )
    assert drain_res.get("terminal_loss", 0) == 1, (
        f"Expected terminal_loss == 1, got {drain_res.get('terminal_loss')}"
    )
    assert _get_terminal_failures() == 1

    # 2. Assert queue clean and task ACKed
    assert _TRACE_QUEUE.unfinished_tasks == 0
    join_done = threading.Event()
    threading.Thread(target=lambda: (_TRACE_QUEUE.join(), join_done.set()), daemon=True).start()
    assert join_done.wait(timeout=1.0) is True

    # 3. Assert DB absence
    fresh_session = SessionLocal()
    try:
        row = fresh_session.query(models.CG01TraceRecord).filter_by(trace_id=t_id).first()
        assert row is None, "Permanently failed trace must NOT exist in DB"
    finally:
        fresh_session.close()

    # 4. Assert all clean
    assert get_in_flight_count() == 0
    assert get_retry_buffer_count() == 0
    assert get_active_batch_count() == 0
    assert get_outstanding_receipt_count() == 0

    stop_trace_persistence_worker()
    await wait_trace_persistence_worker_stopped(timeout_s=2.0)


# ==============================================================================
# TEST 2: TRANSIENT FAILURE THEN SUCCESS (Rules 030, 099, 192)
# ==============================================================================
@pytest.mark.anyio
async def test_red_002_transient_failure_then_retry_success_retains_zero_loss(setup_isolated_db: Session):
    """
    Rules 030, 040, 099, 192:
    Attempt 1 fails, Attempt 2 succeeds.
    Verify:
    - terminal_failures == 0, terminal_loss == 0.
    - status == COMPLETED.
    - Trace persisted in DB.
    """
    ready = await start_trace_persistence_worker(timeout_s=2.0)
    assert ready is True

    t_id = "trc_transient_002"
    trace = CG01EvaluationTrace(trace_id=t_id, symbol="XAUUSDT")

    call_count = 0
    orig_persist = CG01TracePersistenceWriter.persist_trace

    def mock_persist(t, db=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return False  # Fail first attempt
        return orig_persist(t, db=db)  # Succeed on second attempt

    with patch.object(CG01TracePersistenceWriter, "persist_trace", side_effect=mock_persist):
        enqueued = enqueue_trace_persistence(trace)
        assert enqueued is True

        drain_res = await drain_trace_persistence(timeout_s=4.0)

    assert drain_res["status"] == "COMPLETED"
    assert drain_res.get("terminal_loss", 0) == 0
    assert _get_terminal_failures() == 0
    assert get_persist_failures() >= 1  # Transient failure was recorded

    # Trace exists in DB
    fresh_session = SessionLocal()
    try:
        row = fresh_session.query(models.CG01TraceRecord).filter_by(trace_id=t_id).first()
        assert row is not None, "Retried trace must exist in DB after success"
    finally:
        fresh_session.close()

    stop_trace_persistence_worker()
    await wait_trace_persistence_worker_stopped(timeout_s=2.0)


# ==============================================================================
# TEST 3: TWO FAILURES THEN THIRD ATTEMPT SUCCESS (Rule 100)
# ==============================================================================
@pytest.mark.anyio
async def test_red_003_two_failures_then_third_attempt_success(setup_isolated_db: Session):
    """
    Rule 100: Near retry boundary (2 failures, attempt 3 succeeds).
    Verify terminal_failures remains 0 and status is COMPLETED.
    """
    ready = await start_trace_persistence_worker(timeout_s=2.0)
    assert ready is True

    t_id = "trc_near_limit_003"
    trace = CG01EvaluationTrace(trace_id=t_id, symbol="XAUUSDT")

    call_count = 0
    orig_persist = CG01TracePersistenceWriter.persist_trace

    def mock_persist(t, db=None):
        nonlocal call_count
        call_count += 1
        if call_count in (1, 2):
            return False
        return orig_persist(t, db=db)

    with patch.object(CG01TracePersistenceWriter, "persist_trace", side_effect=mock_persist):
        enqueue_trace_persistence(trace)
        drain_res = await drain_trace_persistence(timeout_s=4.0)

    assert drain_res["status"] == "COMPLETED"
    assert drain_res.get("terminal_loss", 0) == 0
    assert _get_terminal_failures() == 0

    stop_trace_persistence_worker()
    await wait_trace_persistence_worker_stopped(timeout_s=2.0)


# ==============================================================================
# TEST 4: DUPLICATE TERMINAL FINALIZE CAS CHECK (Rules 045, 101, 193)
# ==============================================================================
def test_red_004_duplicate_terminal_finalize_does_not_double_count_or_ack():
    """
    Rules 045, 101, 193: Calling finalize_queue_receipt_once on same receipt with TERMINAL_FAILURE
    must increment terminal counter exactly once and call task_done() exactly once.
    """
    q: queue.Queue = queue.Queue()
    q.put("dummy")
    assert q.unfinished_tasks == 1

    rcpt = QueueReceipt(receipt_id="rcpt_dup_term", created_at_ms=1000, queue_ref=q)
    with _RETRY_BUFFER_LOCK:
        _ACTIVE_RECEIPTS[rcpt.receipt_id] = rcpt

    init_term = _get_terminal_failures()

    # First call
    ok1 = finalize_queue_receipt_once(rcpt, "TERMINAL_FAILURE")
    assert ok1 is True
    assert _get_terminal_failures() == init_term + 1
    assert q.unfinished_tasks == 0

    # Second call (duplicate)
    ok2 = finalize_queue_receipt_once(rcpt, "TERMINAL_FAILURE")
    assert ok2 is False
    assert _get_terminal_failures() == init_term + 1  # No double increment!
    assert q.unfinished_tasks == 0  # No double task_done()!


# ==============================================================================
# TEST 5: DUPLICATE COMMITTED FINALIZE (Rules 053, 102)
# ==============================================================================
def test_red_005_duplicate_committed_finalize_does_not_double_ack():
    """
    Rule 102: Calling finalize_queue_receipt_once with COMMITTED twice.
    """
    q: queue.Queue = queue.Queue()
    q.put("dummy")
    rcpt = QueueReceipt(receipt_id="rcpt_dup_comm", created_at_ms=1000, queue_ref=q)
    with _RETRY_BUFFER_LOCK:
        _ACTIVE_RECEIPTS[rcpt.receipt_id] = rcpt

    init_term = _get_terminal_failures()

    ok1 = finalize_queue_receipt_once(rcpt, "COMMITTED")
    assert ok1 is True
    assert _get_terminal_failures() == init_term
    assert q.unfinished_tasks == 0

    ok2 = finalize_queue_receipt_once(rcpt, "COMMITTED")
    assert ok2 is False
    assert _get_terminal_failures() == init_term


# ==============================================================================
# TEST 6: COMMITTED RECEIPT REJECTS LATE TERMINAL FAILURE (Rules 054, 103)
# ==============================================================================
def test_red_006_committed_receipt_rejects_late_terminal_failure():
    """
    Rule 103: Receipt already COMMITTED cannot be converted to TERMINAL_FAILURE.
    """
    q: queue.Queue = queue.Queue()
    q.put("dummy")
    rcpt = QueueReceipt(receipt_id="rcpt_comm_then_term", created_at_ms=1000, queue_ref=q)
    with _RETRY_BUFFER_LOCK:
        _ACTIVE_RECEIPTS[rcpt.receipt_id] = rcpt

    init_term = _get_terminal_failures()

    ok1 = finalize_queue_receipt_once(rcpt, "COMMITTED")
    assert ok1 is True
    assert rcpt.finalized_status == "COMMITTED"

    # Late callback with TERMINAL_FAILURE
    ok2 = finalize_queue_receipt_once(rcpt, "TERMINAL_FAILURE")
    assert ok2 is False
    assert rcpt.finalized_status == "COMMITTED"
    assert _get_terminal_failures() == init_term


# ==============================================================================
# TEST 7: TERMINAL RECEIPT REJECTS LATE COMMITTED (Rules 054, 104)
# ==============================================================================
def test_red_007_terminal_receipt_rejects_late_committed():
    """
    Rule 104: Receipt already TERMINAL_FAILURE cannot be overwritten by COMMITTED.
    """
    q: queue.Queue = queue.Queue()
    q.put("dummy")
    rcpt = QueueReceipt(receipt_id="rcpt_term_then_comm", created_at_ms=1000, queue_ref=q)
    with _RETRY_BUFFER_LOCK:
        _ACTIVE_RECEIPTS[rcpt.receipt_id] = rcpt

    init_term = _get_terminal_failures()

    ok1 = finalize_queue_receipt_once(rcpt, "TERMINAL_FAILURE")
    assert ok1 is True
    assert _get_terminal_failures() == init_term + 1

    ok2 = finalize_queue_receipt_once(rcpt, "COMMITTED")
    assert ok2 is False
    assert rcpt.finalized_status == "TERMINAL_FAILURE"
    assert _get_terminal_failures() == init_term + 1


# ==============================================================================
# TEST 8: RECEIPT NONE (Rules 044, 105)
# ==============================================================================
def test_red_008_receipt_none_handled_cleanly():
    """
    Rule 105: finalize_queue_receipt_once(None, ...) returns False without errors.
    """
    init_term = _get_terminal_failures()
    assert finalize_queue_receipt_once(None, "TERMINAL_FAILURE") is False
    assert _get_terminal_failures() == init_term


# ==============================================================================
# TEST 9: DIRECT WRITE (Rules 055, 106)
# ==============================================================================
def test_red_009_direct_write_does_not_affect_queue_or_terminal_loss():
    """
    Rule 106: Direct persist_trace does not interact with queue receipts or terminal counters.
    """
    init_term = _get_terminal_failures()
    t = CG01EvaluationTrace(trace_id="trc_direct_009", symbol="XAUUSDT")
    ok = CG01TracePersistenceWriter.persist_trace(t)
    assert ok is True
    assert _get_terminal_failures() == init_term


# ==============================================================================
# TEST 10: TWO RECEIPTS WITH SAME TRACE ID (Rules 032, 107, 108)
# ==============================================================================
@pytest.mark.anyio
async def test_red_010_duplicate_trace_id_terminal_exhaustion_tracks_two_receipts():
    """
    Rules 032, 107: Two enqueue operations with same trace_id.
    Both fail terminally -> terminal_failures == 2.
    """
    ready = await start_trace_persistence_worker(timeout_s=2.0)
    assert ready is True

    t_shared = CG01EvaluationTrace(trace_id="trc_shared_dup_010", symbol="XAUUSDT")

    with patch.object(CG01TracePersistenceWriter, "persist_trace", return_value=False):
        enqueue_trace_persistence(t_shared)
        enqueue_trace_persistence(t_shared)

        drain_res = await drain_trace_persistence(timeout_s=4.0)

    assert drain_res["status"] == "COMPLETED_WITH_LOSS"
    assert drain_res.get("terminal_loss", 0) == 2
    assert _get_terminal_failures() == 2
    assert _TRACE_QUEUE.unfinished_tasks == 0

    stop_trace_persistence_worker()
    await wait_trace_persistence_worker_stopped(timeout_s=2.0)


# ==============================================================================
# TEST 11: QUEUE OVERFLOW DROP (Rules 031, 109)
# ==============================================================================
def test_red_011_queue_overflow_drop_distinct_from_terminal_failure():
    """
    Rules 031, 109: Queue full drop increments _TRACE_QUEUE_DROPS, not terminal_failures.
    """
    init_drops = _get_queue_drops()
    init_terms = _get_terminal_failures()

    small_queue: queue.Queue = queue.Queue(maxsize=1)
    small_queue.put("item1")

    with patch("services.cg01_telemetry_service._TRACE_QUEUE", small_queue):
        t = CG01EvaluationTrace(trace_id="trc_drop_011")
        ok = enqueue_trace_persistence(t)
        assert ok is False

    assert _get_queue_drops() == init_drops + 1
    assert _get_terminal_failures() == init_terms


# ==============================================================================
# TEST 12: COMBINED LOSS (Rules 038, 110)
# ==============================================================================
@pytest.mark.anyio
async def test_red_012_combined_queue_drop_and_terminal_retry_failure():
    """
    Rules 038, 110: 1 queue drop + 1 terminal retry failure -> terminal_loss == 2.
    """
    ready = await start_trace_persistence_worker(timeout_s=2.0)
    assert ready is True

    # 1. Queue drop
    small_q: queue.Queue = queue.Queue(maxsize=1)
    small_q.put("dummy")
    with patch("services.cg01_telemetry_service._TRACE_QUEUE", small_q):
        enqueue_trace_persistence(CG01EvaluationTrace(trace_id="trc_dropped_12"))

    assert _get_queue_drops() == 1

    # 2. Terminal retry failure
    with patch.object(CG01TracePersistenceWriter, "persist_trace", return_value=False):
        enqueue_trace_persistence(CG01EvaluationTrace(trace_id="trc_exhausted_12"))
        drain_res = await drain_trace_persistence(timeout_s=4.0)

    assert drain_res["status"] == "COMPLETED_WITH_LOSS"
    assert drain_res.get("terminal_loss", 0) == 2
    assert _get_terminal_failures() == 1
    assert _get_queue_drops() == 1

    stop_trace_persistence_worker()
    await wait_trace_persistence_worker_stopped(timeout_s=2.0)


# ==============================================================================
# TEST 13: BATCH MULTI-ITEM TERMINAL EXHAUSTION (Rule 111)
# ==============================================================================
@pytest.mark.anyio
async def test_red_013_batch_multi_item_terminal_exhaustion():
    """
    Rule 111: Enqueue batch of 4 traces, all fail terminally -> terminal_failures == 4.
    """
    ready = await start_trace_persistence_worker(timeout_s=2.0)
    assert ready is True

    with patch.object(CG01TracePersistenceWriter, "persist_trace", return_value=False):
        for i in range(4):
            enqueue_trace_persistence(CG01EvaluationTrace(trace_id=f"trc_batch_13_{i}"))

        drain_res = await drain_trace_persistence(timeout_s=4.0)

    assert drain_res["status"] == "COMPLETED_WITH_LOSS"
    assert drain_res.get("terminal_loss", 0) == 4
    assert _get_terminal_failures() == 4
    assert _TRACE_QUEUE.unfinished_tasks == 0

    stop_trace_persistence_worker()
    await wait_trace_persistence_worker_stopped(timeout_s=2.0)


# ==============================================================================
# TEST 14: BATCH RETRY SUCCESS (Rule 112)
# ==============================================================================
@pytest.mark.anyio
async def test_red_014_batch_retry_success_clears_terminal_loss():
    """
    Rule 112: Batch of 3 items fails attempt 1, succeeds attempt 2 -> terminal_failures == 0.
    """
    ready = await start_trace_persistence_worker(timeout_s=2.0)
    assert ready is True

    call_count = 0
    orig_persist = CG01TracePersistenceWriter.persist_trace

    def mock_persist(t, db=None):
        nonlocal call_count
        call_count += 1
        if call_count <= 3:  # Fail first batch of 3
            return False
        return orig_persist(t, db=db)

    with patch.object(CG01TracePersistenceWriter, "persist_trace", side_effect=mock_persist):
        for i in range(3):
            enqueue_trace_persistence(CG01EvaluationTrace(trace_id=f"trc_batch_14_{i}"))

        drain_res = await drain_trace_persistence(timeout_s=4.0)

    assert drain_res["status"] == "COMPLETED"
    assert drain_res.get("terminal_loss", 0) == 0
    assert _get_terminal_failures() == 0

    stop_trace_persistence_worker()
    await wait_trace_persistence_worker_stopped(timeout_s=2.0)


# ==============================================================================
# TEST 15: MIXED OUTCOME BATCH (Rule 113)
# ==============================================================================
@pytest.mark.anyio
async def test_red_015_mixed_outcome_batch_one_succeeds_one_fails():
    """
    Rule 113: Item A succeeds on retry, Item B permanently fails ->
    1 committed, 1 terminal failure, status COMPLETED_WITH_LOSS.
    """
    ready = await start_trace_persistence_worker(timeout_s=2.0)
    assert ready is True

    t_succ = CG01EvaluationTrace(trace_id="trc_mixed_succ_15")
    t_fail = CG01EvaluationTrace(trace_id="trc_mixed_fail_15")

    orig_persist = CG01TracePersistenceWriter.persist_trace

    def mock_persist(t, db=None):
        if t.trace_id == "trc_mixed_fail_15":
            return False
        return orig_persist(t, db=db)

    with patch.object(CG01TracePersistenceWriter, "persist_trace", side_effect=mock_persist):
        enqueue_trace_persistence(t_succ)
        enqueue_trace_persistence(t_fail)

        drain_res = await drain_trace_persistence(timeout_s=4.0)

    assert drain_res["status"] == "COMPLETED_WITH_LOSS"
    assert drain_res.get("terminal_loss", 0) == 1
    assert _get_terminal_failures() == 1
    assert get_persisted_count() >= 1

    # Verify DB: t_succ exists, t_fail absent
    s = SessionLocal()
    try:
        assert s.query(models.CG01TraceRecord).filter_by(trace_id="trc_mixed_succ_15").first() is not None
        assert s.query(models.CG01TraceRecord).filter_by(trace_id="trc_mixed_fail_15").first() is None
    finally:
        s.close()

    stop_trace_persistence_worker()
    await wait_trace_persistence_worker_stopped(timeout_s=2.0)


# ==============================================================================
# TEST 16: SESSION OPEN ERROR EXHAUSTION (Rule 114)
# ==============================================================================
@pytest.mark.anyio
async def test_red_016_session_open_error_exhaustion():
    """
    Rule 114: Session factory raises exception across retries -> terminal_failures == 1.
    """
    ready = await start_trace_persistence_worker(timeout_s=2.0)
    assert ready is True

    t = CG01EvaluationTrace(trace_id="trc_sess_err_16")

    def bad_factory():
        raise RuntimeError("DB connection pool exhausted")

    with patch("database.SessionLocal", side_effect=bad_factory):
        enqueue_trace_persistence(t)
        drain_res = await drain_trace_persistence(timeout_s=4.0)

    assert drain_res["status"] == "COMPLETED_WITH_LOSS"
    assert drain_res.get("terminal_loss", 0) == 1
    assert _get_terminal_failures() == 1

    stop_trace_persistence_worker()
    await wait_trace_persistence_worker_stopped(timeout_s=2.0)


# ==============================================================================
# TEST 17: COMMIT ERROR EXHAUSTION (Rule 115)
# ==============================================================================
@pytest.mark.anyio
async def test_red_017_commit_error_exhaustion():
    """
    Rule 115: Session commit raises across retries -> terminal_failures == 1.
    """
    ready = await start_trace_persistence_worker(timeout_s=2.0)
    assert ready is True

    t = CG01EvaluationTrace(trace_id="trc_commit_err_17")

    orig_sf = SessionLocal

    def failing_commit_session():
        s = orig_sf()
        s.commit = MagicMock(side_effect=RuntimeError("Disk I/O error on commit"))
        return s

    with patch("database.SessionLocal", side_effect=failing_commit_session):
        enqueue_trace_persistence(t)
        drain_res = await drain_trace_persistence(timeout_s=4.0)

    assert drain_res["status"] == "COMPLETED_WITH_LOSS"
    assert drain_res.get("terminal_loss", 0) == 1
    assert _get_terminal_failures() == 1

    stop_trace_persistence_worker()
    await wait_trace_persistence_worker_stopped(timeout_s=2.0)


# ==============================================================================
# TEST 18: RESET WORKER STATE (Rules 061, 116)
# ==============================================================================
def test_red_018_reset_telemetry_worker_state_clears_terminal_counters():
    """
    Rule 116: reset_telemetry_worker_state() resets terminal_failures and queue_drops.
    """
    q: queue.Queue = queue.Queue()
    q.put("dummy")
    rcpt = QueueReceipt(receipt_id="rcpt_reset_test", created_at_ms=1000, queue_ref=q)
    finalize_queue_receipt_once(rcpt, "TERMINAL_FAILURE")
    assert _get_terminal_failures() >= 1

    reset_telemetry_worker_state()

    assert _get_terminal_failures() == 0
    assert _get_queue_drops() == 0


# ==============================================================================
# TEST 19: CUMULATIVE SEMANTICS (Rules 074, 075, 117)
# ==============================================================================
@pytest.mark.anyio
async def test_red_019_cumulative_semantics_across_subsequent_drains():
    """
    Rules 074, 117: Subsequent drain in same lifecycle still reflects cumulative terminal loss.
    """
    ready = await start_trace_persistence_worker(timeout_s=2.0)
    assert ready is True

    with patch.object(CG01TracePersistenceWriter, "persist_trace", return_value=False):
        enqueue_trace_persistence(CG01EvaluationTrace(trace_id="trc_cumul_19"))
        d1 = await drain_trace_persistence(timeout_s=4.0)

    assert d1["status"] == "COMPLETED_WITH_LOSS"
    assert d1.get("terminal_loss", 0) == 1

    # Second drain without new work in same lifecycle
    d2 = await drain_trace_persistence(timeout_s=1.0)
    assert d2["status"] == "COMPLETED_WITH_LOSS"
    assert d2.get("terminal_loss", 0) == 1

    stop_trace_persistence_worker()
    await wait_trace_persistence_worker_stopped(timeout_s=2.0)


# ==============================================================================
# TEST 20: SUBSEQUENT SUCCESS DOES NOT CLEAR PRIOR LOSS (Rules 070, 118)
# ==============================================================================
@pytest.mark.anyio
async def test_red_020_subsequent_success_does_not_clear_prior_terminal_loss():
    """
    Rule 118: A subsequent successful commit does not erase prior terminal loss.
    """
    ready = await start_trace_persistence_worker(timeout_s=2.0)
    assert ready is True

    # 1. Loss
    with patch.object(CG01TracePersistenceWriter, "persist_trace", return_value=False):
        enqueue_trace_persistence(CG01EvaluationTrace(trace_id="trc_lost_20"))
        d1 = await drain_trace_persistence(timeout_s=4.0)
    assert d1["status"] == "COMPLETED_WITH_LOSS"

    # 2. Success
    enqueue_trace_persistence(CG01EvaluationTrace(trace_id="trc_succ_20"))
    d2 = await drain_trace_persistence(timeout_s=3.0)

    # Status must still be COMPLETED_WITH_LOSS because cumulative terminal_loss == 1
    assert d2["status"] == "COMPLETED_WITH_LOSS"
    assert d2.get("terminal_loss", 0) == 1
    assert get_persisted_count() >= 1

    stop_trace_persistence_worker()
    await wait_trace_persistence_worker_stopped(timeout_s=2.0)


# ==============================================================================
# TEST 21: SYNC FLUSH PENDING TRACES PARITY (Rules 081, 120)
# ==============================================================================
def test_red_021_sync_flush_pending_traces_parity():
    """
    Rule 120: Sau terminal exhaustion, chạy flush_pending_traces() trong điều kiện worker dừng.
    Assert không trả COMPLETED sạch hoặc terminal_loss = 0.
    """
    assert tel_mod._TRACE_WORKER_RUNNING is False

    # Simulate terminal exhaustion
    q: queue.Queue = queue.Queue()
    q.put("dummy")
    rcpt = QueueReceipt(receipt_id="rcpt_flush_term_21", created_at_ms=1000, queue_ref=q)
    with _RETRY_BUFFER_LOCK:
        _ACTIVE_RECEIPTS[rcpt.receipt_id] = rcpt
    finalize_queue_receipt_once(rcpt, "TERMINAL_FAILURE")
    assert _get_terminal_failures() >= 1

    # Run flush_pending_traces with stopped worker
    res = flush_pending_traces(timeout_s=3.0)

    # Must NOT report COMPLETED cleanly or terminal_loss = 0
    assert res["status"] == "COMPLETED_WITH_LOSS"
    assert res["terminal_loss"] >= 1
    assert res["terminal_failures"] >= 1


# ==============================================================================
# TEST 22: ASYNC DRAIN DELEGATION TO SYNC FLUSH (Rules 084, 121)
# ==============================================================================
@pytest.mark.anyio
async def test_red_022_async_drain_delegation_to_sync_flush_preserves_loss():
    """
    Rule 121: When worker is stopped, drain_trace_persistence delegates to sync flush
    and preserves terminal_loss and COMPLETED_WITH_LOSS.
    """
    assert tel_mod._TRACE_WORKER_RUNNING is False

    # Simulate prior terminal exhaustion
    q: queue.Queue = queue.Queue()
    q.put("dummy")
    rcpt = QueueReceipt(receipt_id="rcpt_delegate_term_22", created_at_ms=1000, queue_ref=q)
    with _RETRY_BUFFER_LOCK:
        _ACTIVE_RECEIPTS[rcpt.receipt_id] = rcpt
    finalize_queue_receipt_once(rcpt, "TERMINAL_FAILURE")
    assert _get_terminal_failures() >= 1

    res = await drain_trace_persistence(timeout_s=3.0)

    # Delegated async drain must preserve COMPLETED_WITH_LOSS and terminal_loss
    assert res["status"] == "COMPLETED_WITH_LOSS"
    assert res["terminal_loss"] >= 1
    assert res["terminal_failures"] >= 1



# ==============================================================================
# TEST 23: CONCURRENT FINALIZE THREAD SAFETY (Rules 049, 123)
# ==============================================================================
def test_red_023_concurrent_finalize_thread_safety():
    """
    Rule 123: Multiple threads concurrently finalizing the same receipt.
    Exactly one succeeds, terminal_failures increments by 1.
    """
    q: queue.Queue = queue.Queue()
    q.put("dummy")
    rcpt = QueueReceipt(receipt_id="rcpt_concurrent_23", created_at_ms=1000, queue_ref=q)
    with _RETRY_BUFFER_LOCK:
        _ACTIVE_RECEIPTS[rcpt.receipt_id] = rcpt

    init_term = _get_terminal_failures()
    results = []

    def worker():
        res = finalize_queue_receipt_once(rcpt, "TERMINAL_FAILURE")
        results.append(res)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    # Exactly one True, 9 False
    assert results.count(True) == 1
    assert results.count(False) == 9
    assert _get_terminal_failures() == init_term + 1
    assert q.unfinished_tasks == 0


# ==============================================================================
# TEST 24: TIMEOUT WITH RECORDED LOSS (Rules 071, 072, 119)
# ==============================================================================
@pytest.mark.anyio
async def test_red_024_timeout_with_recorded_loss():
    """
    Rule 119: Recorded terminal loss exists, but another operation is still blocked.
    Drain times out -> returns status TIMED_OUT (not COMPLETED_WITH_LOSS!) while providing
    full loss breakdown (terminal_loss >= 1).
    """
    # 1. Finalize one receipt with TERMINAL_FAILURE
    q1: queue.Queue = queue.Queue()
    q1.put("item1")
    r1 = QueueReceipt(receipt_id="rcpt_term_24", created_at_ms=1000, queue_ref=q1)
    with _RETRY_BUFFER_LOCK:
        _ACTIVE_RECEIPTS[r1.receipt_id] = r1
    finalize_queue_receipt_once(r1, "TERMINAL_FAILURE")
    assert _get_terminal_failures() >= 1

    # 2. Block an operation task in _ACTIVE_OPERATIONS
    blocker_ev = asyncio.Event()
    async def blocked_op():
        await blocker_ev.wait()
    blocker_task = asyncio.create_task(blocked_op())
    with _RETRY_BUFFER_LOCK:
        tel_mod._ACTIVE_OPERATIONS["op_blocked_24"] = blocker_task

    try:
        # Drain with short timeout (0.15s)
        res = await drain_trace_persistence(timeout_s=0.15)

        # Rules 071, 072: Status must be TIMED_OUT (unfinished work takes precedence over loss conclusion)
        assert res["status"] == "TIMED_OUT"
        assert res.get("terminal_loss", 0) >= 1
        assert res.get("terminal_failures", 0) >= 1
        assert res.get("active_operations", 0) >= 1
    finally:
        blocker_ev.set()
        await blocker_task
        with _RETRY_BUFFER_LOCK:
            tel_mod._ACTIVE_OPERATIONS.pop("op_blocked_24", None)



