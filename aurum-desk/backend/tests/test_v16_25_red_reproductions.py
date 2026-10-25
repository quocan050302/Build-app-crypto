"""
backend/tests/test_v16_25_red_reproductions.py
V16.25 Red Defect Reproductions & Precision Contract Verification:
- Scope A: SUBMITTED-BUT-NOT-STARTED BATCH CANCELLATION (Rules 021-024, 029, 031-060, 101-110).
- Scope B: EXACTLY-ONCE QUEUE ACKNOWLEDGEMENT ACROSS RETRIES (Rules 025-028, 061-080, 111-120).
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
import queue
import sys
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

# Ensure test DB isolation (Rule 012)
os.environ.setdefault("AURUM_DB_PATH", ":memory:")

from database import SessionLocal, engine
from models import Base, CG01TraceRecord
import services.cg01_telemetry_service as tel_mod
from services.cg01_telemetry_service import (
    BatchHandle,
    CG01EvaluationTrace,
    CG01TracePersistenceWriter,
    _ACTIVE_BATCH_HANDLES,
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
    get_active_batch_count,
    get_in_flight_count,
    get_retry_buffer_count,
    reset_telemetry_worker_state,
    run_trace_persistence_worker,
    start_trace_persistence_worker,
    stop_trace_persistence_worker,
    wait_trace_persistence_worker_stopped,
)


@pytest.fixture(autouse=True)
def setup_isolated_db():
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        session.query(CG01TraceRecord).delete()
        session.commit()
    finally:
        session.close()
    reset_telemetry_worker_state()
    yield
    reset_telemetry_worker_state()


# ==============================================================================
# RED 021-024: SATURATED EXECUTOR & SUBMITTED BATCH CANCELLATION (Rules 021-024, 031-050)
# Batch đã submit nhưng thread chưa bắt đầu (SUBMITTED).
# Cancel worker waiter. Blocker release sau đó.
# Assert batch không bị kẹt ở SUBMITTED, và trace được persist đúng.
# ==============================================================================
@pytest.mark.anyio
async def test_red_021_024_saturated_executor_submitted_batch_persists_after_cancel():
    """
    Rules 021-024:
    1. Executor has max_workers=1 occupied by blocker task.
    2. Telemetry batch is submitted (state=SUBMITTED, thread not yet started).
    3. Worker waiter is cancelled while batch is still waiting for executor.
    4. Blocker task is released.
    5. Operation must run to completion, persist trace, decrement in-flight, and clean up handle.
    """
    reset_telemetry_worker_state()
    trace = CG01EvaluationTrace(trace_id="trc_sat_exec_021", symbol="XAUUSDT")
    enqueue_trace_persistence(trace)

    loop = asyncio.get_running_loop()
    custom_executor = ThreadPoolExecutor(max_workers=1)

    blocker_started = threading.Event()
    blocker_release = threading.Event()

    def blocker_task():
        blocker_started.set()
        blocker_release.wait(timeout=5.0)

    custom_executor.submit(blocker_task)
    assert blocker_started.wait(timeout=2.0) is True, "Blocker task must start"

    orig_run_in_executor = loop.run_in_executor

    def custom_run_in_executor(executor, func, *args):
        return orig_run_in_executor(custom_executor, func, *args)

    with patch.object(loop, "run_in_executor", side_effect=custom_run_in_executor):
        worker_task = asyncio.create_task(run_trace_persistence_worker())
        await asyncio.sleep(0.1)

        active_handles = tel_mod.get_active_batch_handles()
        assert len(active_handles) == 1, f"Expected 1 active batch handle, got {len(active_handles)}"
        handle = list(active_handles.values())[0]
        assert handle.state == "SUBMITTED", f"Handle state must be SUBMITTED, got {handle.state}"
        assert handle.trace_ids == ["trc_sat_exec_021"]
        assert get_in_flight_count() >= 1

        # Cancel worker waiter coroutine (Rule 022)
        worker_task.cancel()
        await asyncio.sleep(0.05)

        active_handles_after_cancel = tel_mod.get_active_batch_handles()
        assert len(active_handles_after_cancel) == 1, (
            "Batch handle must NOT be lost or cleared while still submitted in executor!"
        )

        # Release blocker task (Rule 023)
        blocker_release.set()

        start_t = time.monotonic()
        while time.monotonic() - start_t < 3.0:
            if get_active_batch_count() == 0 and get_in_flight_count() == 0:
                break
            await asyncio.sleep(0.05)

        assert get_active_batch_count() == 0, "Active batch handles must be 0 after completion"
        assert get_in_flight_count() == 0, "In-flight count must be 0 after completion"

        db = SessionLocal()
        try:
            row = db.query(CG01TraceRecord).filter(CG01TraceRecord.trace_id == "trc_sat_exec_021").first()
            assert row is not None, "Trace trc_sat_exec_021 must be durably persisted to DB!"
            assert row.symbol == "XAUUSDT"
        finally:
            db.close()

    custom_executor.shutdown(wait=True)


# ==============================================================================
# RED 025: QUEUE RETRY ACK (Rule 025, 061-070)
# Enqueue một trace thật bằng queue API. Cho initial commit fail, retry success.
# Assert original queue task được ACK đúng một lần.
# ==============================================================================
@pytest.mark.anyio
async def test_red_025_queue_retry_acknowledges_original_task():
    """
    Rule 025: Enqueue trace via official queue API.
    Attempt 1 fails (retained in retry buffer).
    Attempt 2 succeeds.
    Original queue task must be ACK'd so unfinished_tasks decrements to 0.
    """
    reset_telemetry_worker_state()
    trace = CG01EvaluationTrace(trace_id="trc_ack_retry_025", symbol="XAUUSDT")
    ok = enqueue_trace_persistence(trace)
    assert ok is True
    assert _TRACE_QUEUE.unfinished_tasks == 1

    attempts = {"count": 0}
    original_persist = CG01TracePersistenceWriter.persist_trace

    def flaky_persist(t, db=None):
        if t.trace_id == "trc_ack_retry_025":
            attempts["count"] += 1
            if attempts["count"] == 1:
                return False  # Fail attempt 1
        return original_persist(t, db=db)

    with patch.object(CG01TracePersistenceWriter, "persist_trace", side_effect=flaky_persist):
        ready = await start_trace_persistence_worker(timeout_s=2.0)
        assert ready is True

        start_t = time.monotonic()
        while time.monotonic() - start_t < 3.0:
            if attempts["count"] >= 2 and tel_mod.get_persisted_count() >= 1:
                break
            await asyncio.sleep(0.05)

        assert attempts["count"] >= 2, f"Expected at least 2 attempts, got {attempts['count']}"
        assert tel_mod.get_persisted_count() >= 1

        assert _TRACE_QUEUE.unfinished_tasks == 0, (
            f"Expected unfinished_tasks == 0 after retry success, but got {_TRACE_QUEUE.unfinished_tasks}"
        )

        stop_trace_persistence_worker()
        await wait_trace_persistence_worker_stopped(timeout_s=2.0)


# ==============================================================================
# RED 026: QUEUE JOIN (Rule 026, 078)
# Sau retry success, queue.join() phải hoàn tất trong timeout mà không treo.
# ==============================================================================
@pytest.mark.anyio
async def test_red_026_queue_join_completes_after_retry_success():
    """
    Rule 026: After retry success, queue.join() must complete without hanging.
    """
    reset_telemetry_worker_state()
    trace = CG01EvaluationTrace(trace_id="trc_join_retry_026", symbol="XAUUSDT")
    enqueue_trace_persistence(trace)

    attempts = {"count": 0}
    original_persist = CG01TracePersistenceWriter.persist_trace

    def flaky_persist(t, db=None):
        if t.trace_id == "trc_join_retry_026":
            attempts["count"] += 1
            if attempts["count"] == 1:
                return False
        return original_persist(t, db=db)

    with patch.object(CG01TracePersistenceWriter, "persist_trace", side_effect=flaky_persist):
        ready = await start_trace_persistence_worker(timeout_s=2.0)
        assert ready is True

        start_t = time.monotonic()
        while time.monotonic() - start_t < 6.0:
            if attempts["count"] >= 2 and tel_mod.get_persisted_count() >= 1:
                break
            await asyncio.sleep(0.05)

        assert tel_mod.get_persisted_count() >= 1

        join_completed = threading.Event()

        def do_join():
            _TRACE_QUEUE.join()
            join_completed.set()

        t = threading.Thread(target=do_join, daemon=True)
        t.start()
        t.join(timeout=1.0)

        assert join_completed.is_set(), (
            "queue.join() failed to complete after retry success! unfinished_tasks was not ACK'd."
        )

        stop_trace_persistence_worker()
        await wait_trace_persistence_worker_stopped(timeout_s=2.0)


# ==============================================================================
# RED 027: TERMINAL FAILURE ACK (Rule 027, 069)
# Mọi retries fail tới ngưỡng policy (attempt 3).
# Original queue task phải kết thúc đúng một lần, không treo join.
# ==============================================================================
@pytest.mark.anyio
async def test_red_027_terminal_failure_acknowledges_queue_task():
    """
    Rule 027: All attempts fail permanently.
    Original queue task must be ACK'd as terminal failure so queue.join() does not hang.
    """
    reset_telemetry_worker_state()
    trace = CG01EvaluationTrace(trace_id="trc_term_fail_027", symbol="XAUUSDT")
    enqueue_trace_persistence(trace)

    with patch.object(CG01TracePersistenceWriter, "persist_trace", return_value=False):
        ready = await start_trace_persistence_worker(timeout_s=2.0)
        assert ready is True

        start_t = time.monotonic()
        while time.monotonic() - start_t < 3.0:
            if get_retry_buffer_count() == 0 and get_in_flight_count() == 0 and _TRACE_PERSIST_FAILURES >= 3:
                break
            await asyncio.sleep(0.05)

        assert _TRACE_QUEUE.unfinished_tasks == 0, (
            f"Expected unfinished_tasks == 0 after terminal failure, but got {_TRACE_QUEUE.unfinished_tasks}"
        )

        join_completed = threading.Event()

        def do_join():
            _TRACE_QUEUE.join()
            join_completed.set()

        t = threading.Thread(target=do_join, daemon=True)
        t.start()
        t.join(timeout=1.0)
        assert join_completed.is_set(), "queue.join() must complete after terminal failure!"

        stop_trace_persistence_worker()
        await wait_trace_persistence_worker_stopped(timeout_s=2.0)


# ==============================================================================
# RED 028: NO DOUBLE ACK (Rule 028, 071, 072)
# Chạy duplicate retry/completion notification có kiểm soát.
# Không task_done hai lần, không catch ValueError rồi pass để che lỗi.
# ==============================================================================
def test_red_028_no_double_ack_on_duplicate_completion():
    """
    Rule 028: Finalizing a QueueReceipt twice must NOT call queue.task_done() twice.
    First call returns True, second returns False.
    """
    from services.cg01_telemetry_service import QueueReceipt, finalize_queue_receipt_once

    q = queue.Queue()
    q.put("dummy")
    assert q.unfinished_tasks == 1

    rcpt = QueueReceipt(receipt_id="rcpt_test_028", created_at_ms=int(time.time() * 1000), queue_ref=q)

    # First finalization
    ok1 = finalize_queue_receipt_once(rcpt, "COMMITTED")
    assert ok1 is True
    assert q.unfinished_tasks == 0

    # Second finalization (duplicate)
    ok2 = finalize_queue_receipt_once(rcpt, "COMMITTED")
    assert ok2 is False
    assert q.unfinished_tasks == 0, "Second call must NOT double decrement unfinished_tasks!"


# ==============================================================================
# RED 029: CANCEL THEN RETRY (Rule 029, 118)
# Submit còn chờ executor, cancel waiter, sau đó actual write fail và retry success.
# Assert ownership, retry và ACK đồng thời đúng.
# ==============================================================================
@pytest.mark.anyio
async def test_red_029_cancel_waiter_then_fail_and_retry_success():
    """
    Rule 029:
    1. Enqueue trace.
    2. Saturated executor holds submitted batch in SUBMITTED state.
    3. Cancel waiter coroutine.
    4. Blocker released, but write fails on attempt 1.
    5. Batch is retained in retry buffer (preserving receipt).
    6. Restart worker (or worker loop) and attempt 2 succeeds.
    7. Exactly 1 row in DB, queue.join() completes, active handles = 0.
    """
    reset_telemetry_worker_state()
    trace = CG01EvaluationTrace(trace_id="trc_cancel_retry_029", symbol="XAUUSDT")
    enqueue_trace_persistence(trace)

    loop = asyncio.get_running_loop()
    custom_executor = ThreadPoolExecutor(max_workers=1)

    blocker_started = threading.Event()
    blocker_release = threading.Event()

    def blocker_task():
        blocker_started.set()
        blocker_release.wait(timeout=5.0)

    custom_executor.submit(blocker_task)
    assert blocker_started.wait(timeout=2.0) is True

    orig_run_in_executor = loop.run_in_executor

    def custom_run_in_executor(executor, func, *args):
        return orig_run_in_executor(custom_executor, func, *args)

    attempts = {"count": 0}
    original_persist = CG01TracePersistenceWriter.persist_trace

    def flaky_persist(t, db=None):
        if t.trace_id == "trc_cancel_retry_029":
            attempts["count"] += 1
            if attempts["count"] == 1:
                return False
        return original_persist(t, db=db)

    with patch.object(loop, "run_in_executor", side_effect=custom_run_in_executor):
        with patch.object(CG01TracePersistenceWriter, "persist_trace", side_effect=flaky_persist):
            worker_task = asyncio.create_task(run_trace_persistence_worker())
            await asyncio.sleep(0.1)

            assert len(tel_mod.get_active_batch_handles()) == 1

            # Cancel waiter while submitted
            worker_task.cancel()
            await asyncio.sleep(0.05)

            # Release blocker; attempt 1 will run in thread and fail
            blocker_release.set()

            # Wait for attempt 1 to fail and transfer to retry buffer
            start_t = time.monotonic()
            while time.monotonic() - start_t < 3.0:
                if get_retry_buffer_count() == 1 and get_active_batch_count() == 0:
                    break
                await asyncio.sleep(0.05)

            assert get_retry_buffer_count() == 1, "Trace must be retained in retry buffer after attempt 1 failure"

    custom_executor.shutdown(wait=True)

    # Now start a new worker generation to process retry attempt 2 (which will succeed)
    with patch.object(CG01TracePersistenceWriter, "persist_trace", side_effect=flaky_persist):
        ready = await start_trace_persistence_worker(timeout_s=2.0)
        assert ready is True

        start_t = time.monotonic()
        while time.monotonic() - start_t < 3.0:
            if attempts["count"] >= 2 and tel_mod.get_persisted_count() >= 1:
                break
            await asyncio.sleep(0.05)

        assert tel_mod.get_persisted_count() >= 1
        assert _TRACE_QUEUE.unfinished_tasks == 0, "Receipt must be ACK'd on retry success!"

        stop_trace_persistence_worker()
        await wait_trace_persistence_worker_stopped(timeout_s=2.0)


# ==============================================================================
# RED 030: DUPLICATE TRACE ID ENQUEUE (Rule 064, 076, 116)
# Hai enqueue operations có cùng trace_id.
# Storage dedupe đúng, hai queue receipts đều hoàn tất.
# ==============================================================================
@pytest.mark.anyio
async def test_red_030_duplicate_trace_id_enqueue_generates_distinct_receipts():
    """
    Rule 064, 076: Enqueue 2 traces with identical trace_id.
    Each enqueue operation gets its own QueueReceipt.
    Both receipts are ACK'd so unfinished_tasks becomes 0.
    """
    reset_telemetry_worker_state()
    t1 = CG01EvaluationTrace(trace_id="trc_dup_030", symbol="XAUUSDT")
    t2 = CG01EvaluationTrace(trace_id="trc_dup_030", symbol="XAUUSDT")

    enqueue_trace_persistence(t1)
    enqueue_trace_persistence(t2)
    assert _TRACE_QUEUE.unfinished_tasks == 2

    ready = await start_trace_persistence_worker(timeout_s=2.0)
    assert ready is True

    start_t = time.monotonic()
    while time.monotonic() - start_t < 3.0:
        if _TRACE_QUEUE.unfinished_tasks == 0:
            break
        await asyncio.sleep(0.05)

    assert _TRACE_QUEUE.unfinished_tasks == 0, (
        f"Both duplicate enqueues must be ACK'd, but unfinished_tasks is {_TRACE_QUEUE.unfinished_tasks}"
    )

    stop_trace_persistence_worker()
    await wait_trace_persistence_worker_stopped(timeout_s=2.0)


# ==============================================================================
# RED 031: DIRECT WRITE DOES NOT ACK QUEUE (Rule 073, 117)
# persist_trace() trực tiếp không qua queue: không có queue receipt, không ACK queue.
# ==============================================================================
def test_red_031_direct_write_does_not_touch_queue_receipts():
    """
    Rule 073: Direct synchronous writes via persist_trace() do NOT touch queue receipts
    and do NOT call queue.task_done().
    """
    reset_telemetry_worker_state()
    trace = CG01EvaluationTrace(trace_id="trc_direct_031", symbol="XAUUSDT")

    # Queue is empty. Calling persist_trace must succeed without ValueError
    ok = CG01TracePersistenceWriter.persist_trace(trace)
    assert ok is True
    assert _TRACE_QUEUE.unfinished_tasks == 0


# ==============================================================================
# RED 032: SUBMITTED RETRY GROUP CANCELLATION (Rule 028, 051, 114)
# Retry group có item 0 đang submitted, items 1 & 2 chưa submit.
# Cancel waiter -> item 0 giữ owner, items 1 & 2 trả về retry buffer.
# ==============================================================================
@pytest.mark.anyio
async def test_red_032_submitted_retry_group_cancellation_preserves_running_and_unstarted():
    """
    Rule 028, 051, 114:
    3 retry items in buffer.
    Item 0 submitted to saturated executor (state=SUBMITTED).
    Items 1 & 2 not yet submitted.
    Cancel waiter.
    Item 0 retains handle/ownership in executor.
    Items 1 & 2 returned to retry buffer.
    """
    reset_telemetry_worker_state()
    t0 = CG01EvaluationTrace(trace_id="trc_retry_group_0", symbol="XAUUSDT")
    t1 = CG01EvaluationTrace(trace_id="trc_retry_group_1", symbol="XAUUSDT")
    t2 = CG01EvaluationTrace(trace_id="trc_retry_group_2", symbol="XAUUSDT")

    with _RETRY_BUFFER_LOCK:
        _RETRY_BUFFER.extend([(t0, 1), (t1, 1), (t2, 1)])

    loop = asyncio.get_running_loop()
    custom_executor = ThreadPoolExecutor(max_workers=1)

    blocker_started = threading.Event()
    blocker_release = threading.Event()

    def blocker_task():
        blocker_started.set()
        blocker_release.wait(timeout=5.0)

    custom_executor.submit(blocker_task)
    assert blocker_started.wait(timeout=2.0) is True

    orig_run_in_executor = loop.run_in_executor

    def custom_run_in_executor(executor, func, *args):
        return orig_run_in_executor(custom_executor, func, *args)

    with patch.object(loop, "run_in_executor", side_effect=custom_run_in_executor):
        worker_task = asyncio.create_task(run_trace_persistence_worker())
        await asyncio.sleep(0.1)

        # Item 0 is submitted
        active_handles = tel_mod.get_active_batch_handles()
        assert len(active_handles) == 1
        handle0 = list(active_handles.values())[0]
        assert handle0.trace_ids == ["trc_retry_group_0"]
        assert handle0.state == "SUBMITTED"

        # Cancel waiter
        worker_task.cancel()
        await asyncio.sleep(0.05)

        # Check: Items 1 & 2 must be returned to retry buffer
        with _RETRY_BUFFER_LOCK:
            retry_ids = [it[0].trace_id for it in _RETRY_BUFFER]
        assert "trc_retry_group_1" in retry_ids
        assert "trc_retry_group_2" in retry_ids
        assert "trc_retry_group_0" not in retry_ids, "Submitted item 0 must NOT be in retry buffer!"

        # Release blocker so item 0 completes
        blocker_release.set()

        start_t = time.monotonic()
        while time.monotonic() - start_t < 3.0:
            if get_active_batch_count() == 0:
                break
            await asyncio.sleep(0.05)

        assert get_active_batch_count() == 0
        db = SessionLocal()
        try:
            row0 = db.query(CG01TraceRecord).filter(CG01TraceRecord.trace_id == "trc_retry_group_0").first()
            assert row0 is not None, "Item 0 must be committed to DB!"
        finally:
            db.close()

    custom_executor.shutdown(wait=True)


# ==============================================================================
# RED 033: DRAIN WAITS FOR SUBMITTED EXECUTOR TASKS (Rule 046, 081, 084)
# Worker stopped, nhưng còn operation queued trong executor.
# Drain không được báo COMPLETED khi operation chưa xong.
# ==============================================================================
@pytest.mark.anyio
async def test_red_033_drain_waits_for_submitted_executor_tasks():
    """
    Rule 046, 081:
    A submitted batch is pending in saturated executor.
    drain_trace_persistence() must wait for it or return TIMED_OUT/IN_FLIGHT,
    and must NOT report COMPLETED prematurely while handle is SUBMITTED.
    """
    reset_telemetry_worker_state()
    trace = CG01EvaluationTrace(trace_id="trc_drain_sub_033", symbol="XAUUSDT")
    enqueue_trace_persistence(trace)

    loop = asyncio.get_running_loop()
    custom_executor = ThreadPoolExecutor(max_workers=1)

    blocker_started = threading.Event()
    blocker_release = threading.Event()

    def blocker_task():
        blocker_started.set()
        blocker_release.wait(timeout=5.0)

    custom_executor.submit(blocker_task)
    assert blocker_started.wait(timeout=2.0) is True

    orig_run_in_executor = loop.run_in_executor

    def custom_run_in_executor(executor, func, *args):
        return orig_run_in_executor(custom_executor, func, *args)

    with patch.object(loop, "run_in_executor", side_effect=custom_run_in_executor):
        worker_task = asyncio.create_task(run_trace_persistence_worker())
        await asyncio.sleep(0.1)

        # Batch is submitted
        assert get_active_batch_count() == 1

        # Drain with short timeout while blocker is still held
        res = await drain_trace_persistence(timeout_s=0.2)
        assert res["status"] in ("TIMED_OUT", "IN_FLIGHT"), (
            f"Drain must NOT return COMPLETED while batch is SUBMITTED in executor! Got {res['status']}"
        )

        # Release blocker and drain cleanly
        blocker_release.set()
        res_clean = await drain_trace_persistence(timeout_s=2.0)
        assert res_clean["status"] == "COMPLETED"

        worker_task.cancel()
        await asyncio.sleep(0.05)

    custom_executor.shutdown(wait=True)
