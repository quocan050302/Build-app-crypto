"""
backend/services/cg01_telemetry_service.py
V16.14 - CG-01 Latency Telemetry, Span Timing, and Trace Recording.

Enforces:
- Rules 031-040: Time contract, monotonic span duration, non-blocking thread-safe ring buffer.
- Rules 041-050: Percentile latency calculation (p50, p95, p99) without hiding tail latency.
- Rules 128: Per-stage and end-to-end breakdown.
"""
from __future__ import annotations

import asyncio
import collections
import copy
import json
import logging
import queue
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)


@dataclass
class CG01Span:
    """Represents a measured phase in the CG-01 detector pipeline."""
    name: str
    start_perf: float
    end_perf: Optional[float] = None
    duration_ms: Optional[float] = None

    def finish(self, end_perf: Optional[float] = None) -> float:
        self.end_perf = end_perf if end_perf is not None else time.perf_counter()
        self.duration_ms = max(0.0, (self.end_perf - self.start_perf) * 1000.0)
        return self.duration_ms


@dataclass
class CG01EvaluationTrace:
    """
    Contract trace connecting market event -> evaluation -> candidate -> armed order -> fill/exit.
    Rules 034, 035.
    """
    trace_id: str
    run_id: Optional[str] = None
    symbol: str = "XAUUSDT"
    observation_mode: str = "SHADOW"  # "SHADOW" or "ACTIVE"
    input_version: Any = 1
    source_event_id: Optional[str] = None
    context_source: str = "CACHE_AUTHORITATIVE"
    model_fingerprint: str = "CG01_V16_18"
    config_fingerprint: str = "DEFAULT"

    # Wall-clock timestamps in UTC ms (Rules 031, 036, 038)
    market_evidence_as_of_ms: Optional[int] = None
    exchange_event_time_ms: Optional[int] = None
    event_received_at: Optional[int] = None
    evidence_known_at: Optional[int] = None
    evidence_available_at: Optional[int] = None
    evaluation_enqueued_at: Optional[int] = None
    evaluation_started_at: Optional[int] = None
    evaluation_completed_at: Optional[int] = None
    arm_request_at: Optional[int] = None
    candidate_persisted_at: Optional[int] = None
    armed_at: Optional[int] = None
    first_fill_eligible_quote_at: Optional[int] = None
    filled_at: Optional[int] = None

    # Granular evaluation stage durations in milliseconds (Rule 037)
    context_span_ms: Optional[float] = None
    poi_span_ms: Optional[float] = None
    sweep_span_ms: Optional[float] = None
    mss_span_ms: Optional[float] = None
    fvg_span_ms: Optional[float] = None
    entry_span_ms: Optional[float] = None
    target_span_ms: Optional[float] = None
    guards_span_ms: Optional[float] = None
    total_runtime_ms: Optional[float] = None

    # Outcome metadata
    candidate_key: Optional[str] = None
    armed_order_id: Optional[str] = None
    evaluation_status: str = "COMPLETED"
    skip_reason: Optional[str] = None

    def calculate_delays(self) -> Dict[str, Optional[float]]:
        """
        Calculate latency metrics adhering to Rules 039 & 041-046.
        Missing milestones stay None rather than being clamped to 0.
        Negative diffs caused by clock skew are marked as None (Rule 100).
        """
        delays: Dict[str, Optional[float]] = {
            "feed_delay_ms": None,
            "candle_availability_delay_ms": None,
            "scheduler_delay_ms": None,
            "detector_runtime_ms": None,
            "arm_delay_ms": None,
            "fill_delay_ms": None,
        }

        # Rule 041, 056 & 100: Feed delay (receive - exchange or evidence time)
        ref_exchange = self.exchange_event_time_ms or self.market_evidence_as_of_ms
        if self.event_received_at is not None and ref_exchange is not None:
            diff = self.event_received_at - ref_exchange
            if diff >= 0:
                delays["feed_delay_ms"] = float(diff)

        # Rule 042: Candle availability delay
        if self.evidence_available_at is not None and self.evidence_known_at is not None:
            diff = self.evidence_available_at - self.evidence_known_at
            if diff >= 0:
                delays["candle_availability_delay_ms"] = float(diff)

        # Rule 043: Scheduler delay (evaluation_started - event_received or available)
        ref_event = self.evidence_available_at or self.event_received_at
        if self.evaluation_started_at is not None and ref_event is not None:
            diff = self.evaluation_started_at - ref_event
            if diff >= 0:
                delays["scheduler_delay_ms"] = float(diff)

        # Rule 044: Detector runtime
        if self.evaluation_completed_at is not None and self.evaluation_started_at is not None:
            diff = self.evaluation_completed_at - self.evaluation_started_at
            if diff >= 0:
                delays["detector_runtime_ms"] = float(diff)
        elif self.total_runtime_ms is not None:
            delays["detector_runtime_ms"] = float(self.total_runtime_ms)

        # Rule 045 & 098: Arm delay (armed_at - arm_request_at or evaluation_completed_at)
        ref_arm_start = self.arm_request_at or self.evaluation_completed_at
        if self.armed_at is not None and ref_arm_start is not None:
            diff = self.armed_at - ref_arm_start
            if diff >= 0:
                delays["arm_delay_ms"] = float(diff)

        # Rule 046 & 099: Fill delay
        if self.filled_at is not None and self.armed_at is not None:
            diff = self.filled_at - self.armed_at
            if diff >= 0:
                delays["fill_delay_ms"] = float(diff)

        return delays

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["delays"] = self.calculate_delays()
        return d


@dataclass
class TraceBatchResult:
    """
    V16.20 Rules 081-085: Structured telemetry batch persistence result.
    Differentiates committed batches from retryable/permanent failures.
    Supports backward-compatible tuple unpacking: (committed_count, failed_count).
    """
    status: str  # "COMMITTED", "RETRYABLE_FAILURE", "PERMANENT_FAILURE", "PARTIAL_REJECTED"
    committed_count: int = 0
    failed_count: int = 0
    retained_for_retry_count: int = 0
    error: Optional[str] = None
    batch_ids: List[str] = field(default_factory=list)

    def __iter__(self):
        return iter((self.committed_count, self.failed_count))


@dataclass
class QueueReceipt:
    """
    V16.25 Rules 061-079: Queue receipt tracking original enqueue acknowledgement.
    Guarantees exactly-once queue task_done() invocation across arbitrary retries and failures.
    Receipt identity is distinct from trace_id (permitting multiple distinct enqueues of same trace).
    """
    receipt_id: str
    created_at_ms: int
    queue_ref: Optional[queue.Queue] = None
    finalized: bool = False
    finalized_status: Optional[str] = None  # "COMMITTED", "TERMINAL_FAILURE", "REJECTED"
    finalized_at_ms: Optional[int] = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def finalize_once(self, status: str) -> bool:
        """
        Rules 068, 069, 071: Atomically finalize receipt exactly once and call task_done() on origin queue.
        Returns True if this call finalized the receipt, False if already finalized (preventing double ACK).
        """
        with self._lock:
            if self.finalized:
                return False
            self.finalized = True
            self.finalized_status = status
            self.finalized_at_ms = int(time.time() * 1000)

        if self.queue_ref is not None:
            # Rule 072: Clean queue invariants; do not swallow ValueError with pass
            self.queue_ref.task_done()
        return True


def finalize_queue_receipt_once(receipt: Optional[QueueReceipt], status: str) -> bool:
    """
    V16.25 & V16.26 Rules 041-054: Atomically finalize receipt once, account terminal loss,
    and remove from active receipts registry.
    Accounting is strictly bound to the first successful finalization.
    """
    if receipt is None:
        return False
    ok = receipt.finalize_once(status)
    if ok:
        with _RETRY_BUFFER_LOCK:
            if status == "TERMINAL_FAILURE":
                global _TRACE_TERMINAL_FAILURES
                _TRACE_TERMINAL_FAILURES += 1
            _ACTIVE_RECEIPTS.pop(receipt.receipt_id, None)
    return ok



class RetryItem(tuple):
    """
    V16.25 Rule 067: Backward-compatible 2-tuple (trace, attempt) that also holds receipt attribute.
    Allows legacy tests and code doing `for t, _ in _RETRY_BUFFER` to work without ValueError,
    while carrying the original QueueReceipt across retries.
    """
    def __new__(cls, trace: CG01EvaluationTrace, attempt: int, receipt: Optional[QueueReceipt] = None):
        obj = super().__new__(cls, (trace, attempt))
        obj.trace = trace
        obj.attempt = attempt
        obj.receipt = receipt
        return obj


@dataclass
class BatchHandle:
    """
    V16.24 & V16.25 Rules 015, 031-050: Batch handle tracking synchronous DB execution.
    Maintains exclusive batch ownership until the synchronous DB operation completes.
    Tracks operation_task and receipts across cancellation and retries.
    """
    batch_id: str
    trace_ids: List[str]
    traces: List[CG01EvaluationTrace]
    receipts: List[Optional[QueueReceipt]] = field(default_factory=list)
    attempt: int = 1
    state: str = "SUBMITTED"  # "SUBMITTED", "RUNNING", "COMMITTED", "FAILED"
    result: Optional[TraceBatchResult] = None
    thread_done_event: threading.Event = field(default_factory=threading.Event)
    operation_task: Optional[asyncio.Task] = None
    is_retry: bool = False


import queue

_TRACE_QUEUE: queue.Queue = queue.Queue(maxsize=10000)
_TRACE_QUEUE_DROPS = 0
_TRACE_PERSIST_FAILURES = 0
_TRACE_PERSISTED_COUNT = 0
_TRACE_TERMINAL_FAILURES = 0
_TRACE_WORKER_RUNNING = False
_TRACE_WORKER_STATE: str = "STOPPED"  # "STOPPED", "STARTING", "RUNNING", "STOPPING", "FAILED"
_TRACE_WORKER_READY_EVENT: Optional[asyncio.Event] = None
_TRACE_WORKER_TASK: Optional[asyncio.Task] = None
_TRACE_WORKER_THREAD: Optional[threading.Thread] = None
_TRACE_WORKER_GENERATION: int = 0
_TRACE_WORKER_STOP_ERROR: Optional[str] = None

# V16.20, V16.24 & V16.25 Rules 031-075: In-flight, retry buffer, active batch handles, receipts & operations
_IN_FLIGHT_COUNT = 0
_RETRY_BUFFER: List[Tuple[Any, ...]] = []  # (trace, attempt_count, Optional[receipt])
_ACTIVE_BATCH_HANDLES: Dict[str, BatchHandle] = {}
_ACTIVE_RECEIPTS: Dict[str, QueueReceipt] = {}
_ACTIVE_OPERATIONS: Dict[str, asyncio.Task] = {}
_RETRY_BUFFER_LOCK = threading.Lock()


def get_in_flight_count() -> int:
    return _IN_FLIGHT_COUNT


def set_in_flight_count(val: int) -> None:
    global _IN_FLIGHT_COUNT
    _IN_FLIGHT_COUNT = max(0, val)


def get_retry_buffer_count() -> int:
    with _RETRY_BUFFER_LOCK:
        return len(_RETRY_BUFFER)


def get_active_batch_count() -> int:
    with _RETRY_BUFFER_LOCK:
        return len(_ACTIVE_BATCH_HANDLES)


def get_active_batch_handles() -> Dict[str, BatchHandle]:
    with _RETRY_BUFFER_LOCK:
        return dict(_ACTIVE_BATCH_HANDLES)


def get_active_receipts() -> Dict[str, QueueReceipt]:
    with _RETRY_BUFFER_LOCK:
        return dict(_ACTIVE_RECEIPTS)


def get_outstanding_receipt_count() -> int:
    with _RETRY_BUFFER_LOCK:
        return len(_ACTIVE_RECEIPTS)


def get_active_operations() -> Dict[str, asyncio.Task]:
    with _RETRY_BUFFER_LOCK:
        return dict(_ACTIVE_OPERATIONS)


def get_active_operations_count() -> int:
    with _RETRY_BUFFER_LOCK:
        return len(_ACTIVE_OPERATIONS)


def get_persisted_count() -> int:
    return _TRACE_PERSISTED_COUNT


def get_persist_failures() -> int:
    return _TRACE_PERSIST_FAILURES


def get_terminal_failure_count() -> int:
    with _RETRY_BUFFER_LOCK:
        return _TRACE_TERMINAL_FAILURES


def get_queue_drops() -> int:
    with _RETRY_BUFFER_LOCK:
        return _TRACE_QUEUE_DROPS


def _get_terminal_loss_total() -> int:
    with _RETRY_BUFFER_LOCK:
        return _TRACE_QUEUE_DROPS + _TRACE_TERMINAL_FAILURES


def _get_loss_accounting_snapshot() -> Dict[str, int]:
    with _RETRY_BUFFER_LOCK:
        qd = _TRACE_QUEUE_DROPS
        tf = _TRACE_TERMINAL_FAILURES
        return {
            "queue_drops": qd,
            "terminal_failures": tf,
            "terminal_loss": qd + tf,
        }



def get_trace_worker_stop_error() -> Optional[str]:
    return _TRACE_WORKER_STOP_ERROR


def get_trace_worker_state() -> str:
    return _TRACE_WORKER_STATE


def is_trace_worker_running() -> bool:
    return _TRACE_WORKER_RUNNING


async def start_trace_persistence_worker(timeout_s: float = 3.0) -> bool:
    """
    V16.23 & V16.24 Rules 086-092: Start background worker with explicit ready handshake.
    - If already RUNNING and task alive: returns True.
    - If STARTING: awaits current generation's ready event (does NOT return True prematurely).
    - If STOPPED / FAILED: creates new task and awaits ready event.
    - If ready event times out or task dies: marks FAILED and returns False.
    """
    global _TRACE_WORKER_STATE, _TRACE_WORKER_READY_EVENT, _TRACE_WORKER_TASK, _TRACE_WORKER_RUNNING
    global _TRACE_WORKER_GENERATION, _TRACE_WORKER_STOP_ERROR

    ready_event = None
    with _RETRY_BUFFER_LOCK:
        if _TRACE_WORKER_STATE == "RUNNING" and _TRACE_WORKER_TASK and not _TRACE_WORKER_TASK.done():
            return True
        if _TRACE_WORKER_STATE == "STARTING" and _TRACE_WORKER_READY_EVENT is not None:
            ready_event = _TRACE_WORKER_READY_EVENT
        else:
            _TRACE_WORKER_GENERATION += 1
            _TRACE_WORKER_STATE = "STARTING"
            _TRACE_WORKER_STOP_ERROR = None
            _TRACE_WORKER_READY_EVENT = asyncio.Event()
            ready_event = _TRACE_WORKER_READY_EVENT
            _TRACE_WORKER_TASK = asyncio.create_task(run_trace_persistence_worker())

    try:
        await asyncio.wait_for(ready_event.wait(), timeout=timeout_s)
        with _RETRY_BUFFER_LOCK:
            return _TRACE_WORKER_STATE == "RUNNING" and _TRACE_WORKER_TASK is not None and not _TRACE_WORKER_TASK.done()
    except asyncio.TimeoutError:
        logger.error(f"Trace persistence worker failed to become ready within {timeout_s}s")
        with _RETRY_BUFFER_LOCK:
            _TRACE_WORKER_STATE = "FAILED"
        return False


async def wait_trace_persistence_worker_stopped(timeout_s: float = 3.0) -> bool:
    """
    V16.23, V16.24 & V16.25 Rules 079, 086, 093, 094: Await clean termination of background worker task.
    Also awaits any lingering active DB threads and operation tasks.
    Records task exception if task terminated due to error.
    """
    global _TRACE_WORKER_TASK, _TRACE_WORKER_STOP_ERROR
    start_t = time.monotonic()
    while (time.monotonic() - start_t) < timeout_s:
        with _RETRY_BUFFER_LOCK:
            task_done = (_TRACE_WORKER_TASK is None or _TRACE_WORKER_TASK.done())
            active_batches = len(_ACTIVE_BATCH_HANDLES)
            active_ops = len(_ACTIVE_OPERATIONS)
        if task_done and active_batches == 0 and active_ops == 0:
            if _TRACE_WORKER_TASK and _TRACE_WORKER_TASK.done() and not _TRACE_WORKER_TASK.cancelled():
                try:
                    exc = _TRACE_WORKER_TASK.exception()
                    if exc:
                        _TRACE_WORKER_STOP_ERROR = str(exc)
                except Exception:
                    pass
            return True
        await asyncio.sleep(0.05)
    return False


def reset_telemetry_worker_state() -> None:
    """Reset telemetry worker globals cleanly for test isolation."""
    global _TRACE_WORKER_RUNNING, _TRACE_WORKER_STATE, _TRACE_WORKER_TASK, _TRACE_WORKER_READY_EVENT
    global _IN_FLIGHT_COUNT, _TRACE_PERSISTED_COUNT, _TRACE_PERSIST_FAILURES, _TRACE_QUEUE_DROPS
    global _TRACE_TERMINAL_FAILURES, _TRACE_WORKER_STOP_ERROR
    with _RETRY_BUFFER_LOCK:
        _TRACE_WORKER_RUNNING = False
        _TRACE_WORKER_STATE = "STOPPED"
        _TRACE_WORKER_TASK = None
        _TRACE_WORKER_READY_EVENT = None
        _TRACE_WORKER_STOP_ERROR = None
        _IN_FLIGHT_COUNT = 0
        _TRACE_PERSISTED_COUNT = 0
        _TRACE_PERSIST_FAILURES = 0
        _TRACE_QUEUE_DROPS = 0
        _TRACE_TERMINAL_FAILURES = 0
        _RETRY_BUFFER.clear()
        _ACTIVE_BATCH_HANDLES.clear()
        for r in list(_ACTIVE_RECEIPTS.values()):
            try:
                r.finalize_once("REJECTED")
            except Exception:
                pass
        _ACTIVE_RECEIPTS.clear()
        _ACTIVE_OPERATIONS.clear()
    while not _TRACE_QUEUE.empty():
        try:
            _TRACE_QUEUE.get_nowait()
            try:
                _TRACE_QUEUE.task_done()
            except ValueError:
                pass
        except queue.Empty:
            break


class CG01TracePersistenceWriter:
    """
    Durable SQLite trace persistence writer (Appendix B, Rules 027, 031-037, 122, 123).
    Persists evaluation traces in dedicated sessions so latency history survives restarts.
    CRITICAL: Never commits caller's database session (Rule 032).
    """
    @staticmethod
    def persist_trace(trace: CG01EvaluationTrace, db: Optional[Any] = None) -> bool:
        global _TRACE_PERSISTED_COUNT, _TRACE_PERSIST_FAILURES
        should_close = False
        target_db = db
        if target_db is None:
            try:
                from database import SessionLocal
                target_db = SessionLocal()
                should_close = True
            except Exception as e:
                _TRACE_PERSIST_FAILURES += 1
                logger.error(f"Failed to open session for trace persistence: {e}")
                return False

        try:
            import models
            delays = trace.calculate_delays()
            mode_val = getattr(trace, "observation_mode", "SHADOW") or "SHADOW"
            created_ts = trace.evaluation_completed_at or trace.evaluation_started_at or int(time.time() * 1000)

            rec = models.CG01TraceRecord(
                trace_id=trace.trace_id,
                symbol=trace.symbol or "XAUUSDT",
                run_id=trace.run_id,
                observation_mode=mode_val,
                source_event_id=trace.source_event_id,
                input_version=str(trace.input_version),
                evaluation_status=trace.evaluation_status or "COMPLETED",
                candidate_key=trace.candidate_key,
                armed_order_id=trace.armed_order_id,
                total_runtime_ms=trace.total_runtime_ms or delays.get("detector_runtime_ms"),
                feed_delay_ms=delays.get("feed_delay_ms"),
                scheduler_delay_ms=delays.get("scheduler_delay_ms"),
                arm_delay_ms=delays.get("arm_delay_ms"),
                fill_delay_ms=delays.get("fill_delay_ms"),
                payload_json=json.dumps(trace.to_dict(), ensure_ascii=False),
                created_at_ms=created_ts,
            )
            target_db.merge(rec)
            if should_close:
                # Dedicated session: safe to commit (Rules 027, 051, 052)
                target_db.commit()
                _TRACE_PERSISTED_COUNT += 1
            else:
                # Caller's session: ONLY flush, NEVER commit, NEVER count as committed here! (Rules 031, 051)
                target_db.flush()

            return True
        except Exception as e:
            _TRACE_PERSIST_FAILURES += 1
            if should_close and target_db is not None:
                try:
                    target_db.rollback()
                except Exception:
                    pass
            logger.warning(f"Failed to persist CG-01 trace {trace.trace_id}: {e}")
            return False
        finally:
            if should_close and target_db is not None:
                target_db.close()

    @staticmethod
    def load_traces(
        symbol: Optional[str] = None,
        run_id: Optional[str] = None,
        mode: Optional[str] = None,
        limit: int = 100,
        db: Optional[Any] = None,
    ) -> List[CG01EvaluationTrace]:
        """
        Truthfully load persisted traces from SQLite without zeroing or corrupting timestamps.
        (Rules 027, 091-093).
        """
        should_close = False
        target_db = db
        if target_db is None:
            try:
                from database import SessionLocal
                target_db = SessionLocal()
                should_close = True
            except Exception:
                return []

        try:
            import models
            query = target_db.query(models.CG01TraceRecord)
            if symbol:
                query = query.filter(models.CG01TraceRecord.symbol == symbol)
            if run_id:
                query = query.filter(models.CG01TraceRecord.run_id == run_id)
            if mode:
                query = query.filter(models.CG01TraceRecord.observation_mode == mode)

            rows = query.order_by(models.CG01TraceRecord.created_at_ms.desc()).limit(limit).all()

            traces: List[CG01EvaluationTrace] = []
            for r in rows:
                p: Dict[str, Any] = {}
                if r.payload_json:
                    try:
                        p = json.loads(r.payload_json)
                    except Exception:
                        p = {}

                # Rule 092: Truthfully restore timestamps from original payload
                t_started = p.get("evaluation_started_at")
                t_completed = p.get("evaluation_completed_at")
                if t_started is None and t_completed is None:
                    # Fallback to created_at_ms only if completely absent in payload
                    t_started = r.created_at_ms
                    t_completed = r.created_at_ms

                t = CG01EvaluationTrace(
                    trace_id=r.trace_id,
                    symbol=r.symbol or "XAUUSDT",
                    run_id=r.run_id,
                    observation_mode=getattr(r, "observation_mode", "SHADOW") or p.get("observation_mode", "SHADOW"),
                    source_event_id=r.source_event_id or p.get("source_event_id"),
                    input_version=r.input_version or p.get("input_version", 1),
                    evaluation_status=r.evaluation_status or p.get("evaluation_status", "COMPLETED"),
                    candidate_key=r.candidate_key or p.get("candidate_key"),
                    armed_order_id=r.armed_order_id or p.get("armed_order_id"),
                    total_runtime_ms=r.total_runtime_ms if r.total_runtime_ms is not None else p.get("total_runtime_ms"),
                    evaluation_started_at=t_started,
                    evaluation_completed_at=t_completed,
                    exchange_event_time_ms=p.get("exchange_event_time_ms"),
                    event_received_at=p.get("event_received_at"),
                    evidence_known_at=p.get("evidence_known_at"),
                    evidence_available_at=p.get("evidence_available_at"),
                    evaluation_enqueued_at=p.get("evaluation_enqueued_at"),
                    arm_request_at=p.get("arm_request_at"),
                    armed_at=p.get("armed_at"),
                    first_fill_eligible_quote_at=p.get("first_fill_eligible_quote_at"),
                    filled_at=p.get("filled_at"),
                    skip_reason=p.get("skip_reason"),
                )
                traces.append(t)
            return traces
        except Exception as e:
            logger.warning(f"Failed to load persisted traces: {e}")
            return []
        finally:
            if should_close and target_db is not None:
                target_db.close()


def enqueue_trace_persistence(trace: CG01EvaluationTrace) -> bool:
    """
    Appendix B, Rule 033, V16.25 Rules 061-079: Non-blocking enqueue of immutable trace snapshot
    along with authoritative QueueReceipt for exactly-once queue task acknowledgement.
    """
    global _TRACE_QUEUE_DROPS
    receipt_id = f"rcpt_{trace.trace_id}_{int(time.time()*1000)}_{uuid.uuid4().hex[:6]}"
    receipt = QueueReceipt(
        receipt_id=receipt_id,
        created_at_ms=int(time.time() * 1000),
        queue_ref=_TRACE_QUEUE,
    )
    with _RETRY_BUFFER_LOCK:
        _ACTIVE_RECEIPTS[receipt_id] = receipt

    try:
        t_copy = copy.deepcopy(trace)
        _TRACE_QUEUE.put_nowait((t_copy, receipt))
        return True
    except queue.Full:
        with _RETRY_BUFFER_LOCK:
            _TRACE_QUEUE_DROPS += 1
            _ACTIVE_RECEIPTS.pop(receipt_id, None)
        logger.warning("Trace persistence queue full; dropped trace")
        return False
    except Exception as e:
        with _RETRY_BUFFER_LOCK:
            _ACTIVE_RECEIPTS.pop(receipt_id, None)
        logger.warning(f"Failed to enqueue trace persistence: {e}")
        return False


def flush_pending_traces(timeout_s: float = 5.0) -> Dict[str, Any]:
    """
    Appendix B, Rules 025-028, 066-105: Flush all pending traces within monotonic timeout.
    Enforces Single-Consumer Drain:
    - If background worker is STARTING, RUNNING, or STOPPING, worker is the sole consumer; flush waits for worker.
    - If background worker is STOPPED, flush acts as the sole consumer to persist remaining items.
    Truthfully accounts for succeeded vs failed writes, and tracks in-flight and retrying writes.
    Does NOT report COMPLETED prematurely if an in-flight batch is committing.
    Reconciles status to COMPLETED_WITH_LOSS when terminal telemetry loss occurred.
    """
    start_t = time.monotonic()
    flushed = 0
    failed = 0

    if _TRACE_WORKER_RUNNING or _TRACE_WORKER_STATE in ("STARTING", "RUNNING", "STOPPING"):
        # Worker is the single consumer (Rules 032, 035, 062, 063). Wait for worker to drain.
        while (
            not _TRACE_QUEUE.empty()
            or get_retry_buffer_count() > 0
            or _IN_FLIGHT_COUNT > 0
            or get_active_batch_count() > 0
            or get_active_operations_count() > 0
            or get_outstanding_receipt_count() > 0
        ) and (time.monotonic() - start_t) < timeout_s:
            time.sleep(0.05)
    else:
        # Worker is stopped. Flush becomes the sole consumer.
        # 1. Drain existing retry buffer if any
        with _RETRY_BUFFER_LOCK:
            retries = list(_RETRY_BUFFER)
            _RETRY_BUFFER.clear()

        for item in retries:
            trace = item[0]
            attempt = item[1]
            receipt = getattr(item, "receipt", None)
            if receipt is None and len(item) >= 3:
                receipt = item[2]
            res = _persist_trace_batch_sync([trace], attempt=attempt, receipts=[receipt] if receipt else None)
            flushed += res.committed_count
            failed += res.failed_count

        # 2. Drain in-queue items
        while not _TRACE_QUEUE.empty() and (time.monotonic() - start_t) < timeout_s:
            try:
                q_item = _TRACE_QUEUE.get_nowait()
            except queue.Empty:
                break

            if isinstance(q_item, tuple) and len(q_item) == 2 and isinstance(q_item[1], QueueReceipt):
                t, r = q_item
            elif isinstance(q_item, CG01EvaluationTrace):
                t = q_item
                r_id = f"rcpt_flush_{t.trace_id}_{int(time.time()*1000)}_{uuid.uuid4().hex[:6]}"
                r = QueueReceipt(receipt_id=r_id, created_at_ms=int(time.time()*1000), queue_ref=_TRACE_QUEUE)
                with _RETRY_BUFFER_LOCK:
                    _ACTIVE_RECEIPTS[r_id] = r
            else:
                t = q_item
                r = None

            try:
                res = _persist_trace_batch_sync([t], receipts=[r] if r else None)
                flushed += res.committed_count
                failed += res.failed_count
            except Exception as e:
                failed += 1
                if r:
                    finalize_queue_receipt_once(r, "TERMINAL_FAILURE")
                logger.warning(f"Error during trace flush: {e}")

        # Wait for any in-flight writes to finish or timeout
        while _IN_FLIGHT_COUNT > 0 and (time.monotonic() - start_t) < timeout_s:
            time.sleep(0.05)

    pending = _TRACE_QUEUE.qsize()
    retry_pending = get_retry_buffer_count()
    current_inflight = _IN_FLIGHT_COUNT
    active_batches = get_active_batch_count()
    active_ops = get_active_operations_count()
    outstanding_receipts = get_outstanding_receipt_count()

    all_clean = (
        pending == 0
        and current_inflight == 0
        and retry_pending == 0
        and active_batches == 0
        and active_ops == 0
        and outstanding_receipts == 0
    )

    loss_snap = _get_loss_accounting_snapshot()
    term_loss = loss_snap["terminal_loss"]

    if failed > 0 and not all_clean:
        status = "FAILED"
    elif all_clean:
        status = "COMPLETED_WITH_LOSS" if term_loss > 0 else "COMPLETED"
    elif (time.monotonic() - start_t) >= timeout_s:
        status = "TIMED_OUT"
    elif current_inflight > 0 or active_batches > 0 or active_ops > 0 or outstanding_receipts > 0:
        status = "IN_FLIGHT"
    else:
        status = "TIMED_OUT"


    return {
        "status": status,
        "flushed": flushed,
        "committed": flushed,
        "failed": failed,
        "pending": pending,
        "owned": current_inflight,
        "in_flight": current_inflight,
        "retry_buffer": retry_pending,
        "retrying": retry_pending,
        "active_batches": active_batches,
        "active_operations": active_ops,
        "outstanding_receipts": outstanding_receipts,
        "terminal_loss": term_loss,
        "terminal_failures": loss_snap["terminal_failures"],
        "queue_drops": loss_snap["queue_drops"],
        "elapsed_sec": round(time.monotonic() - start_t, 3),
        "diagnostics": {
            "worker_running": _TRACE_WORKER_RUNNING,
            "worker_state": _TRACE_WORKER_STATE,
            "queue_drops": loss_snap["queue_drops"],
            "persist_failures": _TRACE_PERSIST_FAILURES,
            "terminal_failures": loss_snap["terminal_failures"],
        },
    }


async def drain_trace_persistence(timeout_s: float = 5.0) -> Dict[str, Any]:
    """
    V16.22 - V16.25 Rules 013, 025, 031-050, 061-085: Asynchronous non-blocking drain for trace persistence.
    Awaits queue, retry buffer, and in-flight commits using asyncio.sleep without blocking the event loop.
    Enforces Single-Consumer Drain:
    - If worker is STARTING, awaits ready handshake before checking work.
    - If worker is RUNNING/STOPPING or has active submitted/running operations, awaits completion without stealing items.
    - If worker is STOPPED/FAILED and no active operations remain, delegates remaining drain to thread safely.
    """
    start_t = time.monotonic()
    boundary_token = f"bnd_{int(start_t * 1000)}"

    # If worker is starting, wait for it to become ready
    if _TRACE_WORKER_STATE == "STARTING" and _TRACE_WORKER_READY_EVENT is not None:
        rem_ready = max(0.05, timeout_s - (time.monotonic() - start_t))
        try:
            await asyncio.wait_for(_TRACE_WORKER_READY_EVENT.wait(), timeout=rem_ready)
        except asyncio.TimeoutError:
            pass

    has_active_work = (
        _TRACE_WORKER_RUNNING
        or _TRACE_WORKER_STATE in ("RUNNING", "STOPPING")
        or get_active_batch_count() > 0
        or get_active_operations_count() > 0
    )


    if has_active_work:
        # Worker/executor owns active operations. Await completion asynchronously without stealing items (Rules 046, 081)
        while (
            not _TRACE_QUEUE.empty()
            or get_retry_buffer_count() > 0
            or _IN_FLIGHT_COUNT > 0
            or get_active_batch_count() > 0
            or get_active_operations_count() > 0
            or get_outstanding_receipt_count() > 0
        ) and (time.monotonic() - start_t) < timeout_s:
            await asyncio.sleep(0.05)
    else:
        # Worker is stopped and no active operations exist. Delegate synchronous drain to thread safely
        rem_timeout = max(0.1, timeout_s - (time.monotonic() - start_t))
        res = await asyncio.to_thread(flush_pending_traces, rem_timeout)
        loss_snap = _get_loss_accounting_snapshot()
        res["boundary"] = boundary_token
        res["owned"] = res.get("in_flight", 0)
        res["terminal_loss"] = loss_snap["terminal_loss"]
        res["terminal_failures"] = loss_snap["terminal_failures"]
        res["queue_drops"] = loss_snap["queue_drops"]
        res["committed"] = res.get("flushed", 0)
        res["worker_state"] = _TRACE_WORKER_STATE
        res["diagnostics"] = {
            "worker_running": _TRACE_WORKER_RUNNING,
            "worker_state": _TRACE_WORKER_STATE,
            "queue_drops": loss_snap["queue_drops"],
            "persist_failures": _TRACE_PERSIST_FAILURES,
            "terminal_failures": loss_snap["terminal_failures"],
        }
        return res

    pending = _TRACE_QUEUE.qsize()
    retry_pending = get_retry_buffer_count()
    current_inflight = _IN_FLIGHT_COUNT
    active_batches = get_active_batch_count()
    active_ops = get_active_operations_count()
    outstanding_receipts = get_outstanding_receipt_count()

    all_clean = (
        pending == 0
        and current_inflight == 0
        and retry_pending == 0
        and active_batches == 0
        and active_ops == 0
        and outstanding_receipts == 0
    )

    loss_snap = _get_loss_accounting_snapshot()
    term_loss = loss_snap["terminal_loss"]

    if all_clean:
        status = "COMPLETED_WITH_LOSS" if term_loss > 0 else "COMPLETED"
    elif (time.monotonic() - start_t) >= timeout_s:
        status = "TIMED_OUT"
    elif current_inflight > 0 or active_batches > 0 or active_ops > 0 or outstanding_receipts > 0:
        status = "IN_FLIGHT"
    else:
        status = "TIMED_OUT"

    return {
        "status": status,
        "boundary": boundary_token,
        "committed": _TRACE_PERSISTED_COUNT,
        "flushed": _TRACE_PERSISTED_COUNT,
        "failed": _TRACE_PERSIST_FAILURES,
        "pending": pending,
        "owned": current_inflight,
        "in_flight": current_inflight,
        "retry_buffer": retry_pending,
        "retrying": retry_pending,
        "active_batches": active_batches,
        "active_operations": active_ops,
        "outstanding_receipts": outstanding_receipts,
        "terminal_loss": term_loss,
        "terminal_failures": loss_snap["terminal_failures"],
        "queue_drops": loss_snap["queue_drops"],
        "elapsed_sec": round(time.monotonic() - start_t, 3),
        "worker_state": _TRACE_WORKER_STATE,
        "diagnostics": {
            "worker_running": _TRACE_WORKER_RUNNING,
            "worker_state": _TRACE_WORKER_STATE,
            "queue_drops": loss_snap["queue_drops"],
            "persist_failures": _TRACE_PERSIST_FAILURES,
            "terminal_failures": loss_snap["terminal_failures"],
        },
    }


def _persist_trace_batch_sync(
    batch: List[CG01EvaluationTrace],
    session_factory: Optional[Any] = None,
    attempt: int = 1,
    already_accounted_inflight: bool = False,
    handle: Optional[BatchHandle] = None,
    receipts: Optional[List[Optional[QueueReceipt]]] = None,
) -> TraceBatchResult:
    """
    Rules 025, 026, 061-075, 081-085, 087: Persist trace batch in worker-owned session,
    committing only upon complete batch success.
    - session_factory invocation inside try: block (catches session-open errors cleanly).
    - If persist_trace returns False for any record, treats as batch failure (rollback & retry).
    - Only ACKs queue items after successful commit or terminal drop via QueueReceipt.
    - Decrements in-flight count in finally: guaranteed.
    - Updates handle state and signals completion if handle is provided.
    """
    global _TRACE_PERSISTED_COUNT, _TRACE_PERSIST_FAILURES, _IN_FLIGHT_COUNT
    if not already_accounted_inflight:
        with _RETRY_BUFFER_LOCK:
            _IN_FLIGHT_COUNT += len(batch)

    if handle:
        handle.state = "RUNNING"

    batch_receipts: List[Optional[QueueReceipt]] = []
    if receipts is not None:
        batch_receipts = list(receipts)
    elif handle and handle.receipts:
        batch_receipts = list(handle.receipts)
    else:
        batch_receipts = [None] * len(batch)

    db = None
    persisted = 0
    failed = 0
    batch_ids = [t.trace_id for t in batch]

    try:
        from database import SessionLocal
        sf = session_factory or SessionLocal
        db = sf()

        for t in batch:
            ok = CG01TracePersistenceWriter.persist_trace(t, db=db)
            if ok:
                persisted += 1
            else:
                failed += 1

        if failed > 0:
            # Rule 026 / 071: Helper returned False for some record; fail batch to prevent partial commit
            raise RuntimeError(f"Trace persistence failed for {failed}/{len(batch)} records in batch")

        db.commit()

        # ACK ONLY AFTER SUCCESSFUL COMMIT VIA QUEUE RECEIPTS! (Rules 061, 068, 071, 072)
        _TRACE_PERSISTED_COUNT += persisted
        for r in batch_receipts:
            if r is not None:
                finalize_queue_receipt_once(r, "COMMITTED")

        res = TraceBatchResult(
            status="COMMITTED",
            committed_count=persisted,
            failed_count=0,
            retained_for_retry_count=0,
            batch_ids=batch_ids,
        )
        if handle:
            handle.state = "COMMITTED"
            handle.result = res
        return res
    except Exception as be:
        if db is not None:
            try:
                if hasattr(db, "rollback"):
                    db.rollback()
            except Exception:
                pass
        logger.warning(f"Error persisting trace batch (attempt {attempt}): {be}")
        _TRACE_PERSIST_FAILURES += len(batch)

        retained = 0
        if attempt < 3:
            # Retain batch for retry! DO NOT call task_done() (Rule 067, 070)
            with _RETRY_BUFFER_LOCK:
                for t, r in zip(batch, batch_receipts):
                    _RETRY_BUFFER.append(RetryItem(t, attempt + 1, r))
                    retained += 1
            status = "RETRYABLE_FAILURE"
        else:
            # Terminal dead-letter / dropped after max retries (Rule 069)
            for r in batch_receipts:
                if r is not None:
                    finalize_queue_receipt_once(r, "TERMINAL_FAILURE")
            status = "PERMANENT_FAILURE"

        res = TraceBatchResult(
            status=status,
            committed_count=0,
            failed_count=len(batch),
            retained_for_retry_count=retained,
            error=str(be),
            batch_ids=batch_ids,
        )
        if handle:
            handle.state = "FAILED"
            handle.result = res
        return res
    finally:
        with _RETRY_BUFFER_LOCK:
            _IN_FLIGHT_COUNT = max(0, _IN_FLIGHT_COUNT - len(batch))
            if handle and handle.batch_id in _ACTIVE_BATCH_HANDLES:
                _ACTIVE_BATCH_HANDLES.pop(handle.batch_id, None)
        if handle:
            handle.thread_done_event.set()
        if db is not None:
            try:
                if hasattr(db, "close"):
                    db.close()
            except Exception:
                pass


async def run_trace_persistence_worker() -> None:
    """
    Appendix B, Rules 021-029, 031-050, 061-085:
    Background worker draining trace queue with isolated DB sessions.
    Executes synchronous database operations in thread pool without blocking asyncio event loop.
    Wraps submitted operations in tracked asyncio Tasks so cancellation of the worker waiter
    never abandons in-flight or submitted batches in the executor.
    Prioritizes and retries batches in _RETRY_BUFFER with original QueueReceipts.
    """
    global _TRACE_WORKER_RUNNING, _IN_FLIGHT_COUNT, _TRACE_PERSIST_FAILURES, _TRACE_PERSISTED_COUNT
    global _TRACE_WORKER_STATE, _TRACE_WORKER_TASK
    with _RETRY_BUFFER_LOCK:
        if _TRACE_WORKER_RUNNING and _TRACE_WORKER_STATE == "RUNNING":
            logger.warning("Trace persistence worker already running; skipping duplicate start.")
            return
        _TRACE_WORKER_RUNNING = True
        _TRACE_WORKER_STATE = "RUNNING"
        try:
            _TRACE_WORKER_TASK = asyncio.current_task()
        except RuntimeError:
            _TRACE_WORKER_TASK = None

    if _TRACE_WORKER_READY_EVENT is not None and not _TRACE_WORKER_READY_EVENT.is_set():
        _TRACE_WORKER_READY_EVENT.set()

    logger.info("CG01 trace persistence worker started and ready.")
    try:
        while _TRACE_WORKER_RUNNING:
            try:
                # 1. Drain retries first with atomic ownership transfer (Rules 026, 034, 035, 044, 051)
                retry_items: List[Tuple[Any, ...]] = []
                with _RETRY_BUFFER_LOCK:
                    if _RETRY_BUFFER:
                        retry_items = _RETRY_BUFFER[:10]
                        del _RETRY_BUFFER[:len(retry_items)]
                        _IN_FLIGHT_COUNT += len(retry_items)

                unprocessed_retries = list(retry_items)
                while unprocessed_retries:
                    item = unprocessed_retries.pop(0)
                    trace = item[0]
                    attempt = item[1]
                    receipt = getattr(item, "receipt", None)
                    if receipt is None and len(item) >= 3:
                        receipt = item[2]

                    batch_id = f"batch_retry_{trace.trace_id}_{int(time.time()*1000)}_{uuid.uuid4().hex[:4]}"
                    handle = BatchHandle(
                        batch_id=batch_id,
                        trace_ids=[trace.trace_id],
                        traces=[trace],
                        receipts=[receipt],
                        attempt=attempt,
                        state="SUBMITTED",
                        is_retry=True,
                    )
                    with _RETRY_BUFFER_LOCK:
                        _ACTIVE_BATCH_HANDLES[batch_id] = handle

                    def _run_retry_sync(b_id=batch_id, b_handle=handle, b_trace=trace, b_att=attempt, b_rcpt=receipt):
                        return _persist_trace_batch_sync([b_trace], None, b_att, True, b_handle, receipts=[b_rcpt])

                    op_task = asyncio.create_task(asyncio.to_thread(_run_retry_sync))
                    handle.operation_task = op_task
                    with _RETRY_BUFFER_LOCK:
                        _ACTIVE_OPERATIONS[batch_id] = op_task

                    def _make_retry_done(b_id=batch_id):
                        def _on_done(t: asyncio.Task):
                            with _RETRY_BUFFER_LOCK:
                                _ACTIVE_OPERATIONS.pop(b_id, None)
                            if not t.cancelled():
                                exc = t.exception()
                                if exc:
                                    logger.error(f"Retry batch {b_id} task exception: {exc}")
                        return _on_done

                    op_task.add_done_callback(_make_retry_done(batch_id))

                    try:
                        await asyncio.shield(op_task)
                    except asyncio.CancelledError:
                        # V16.24 & V16.25 Rules 028, 051, 070: Waiter cancelled while handle is submitted/running!
                        # Shielded op_task continues running/queued in executor and retains batch ownership.
                        # Only return unprocessed_retries (unstarted items) to retry buffer:
                        with _RETRY_BUFFER_LOCK:
                            for it in reversed(unprocessed_retries):
                                if isinstance(it, RetryItem):
                                    _RETRY_BUFFER.insert(0, it)
                                else:
                                    t = it[0]
                                    a = it[1]
                                    r = it[2] if len(it) == 3 else getattr(it, "receipt", None)
                                    _RETRY_BUFFER.insert(0, RetryItem(t, a, r))
                            _IN_FLIGHT_COUNT = max(0, _IN_FLIGHT_COUNT - len(unprocessed_retries))
                        raise
                    except Exception as err:
                        logger.error(f"Error persisting retry trace {trace.trace_id}: {err}")

                # 2. Drain new items from queue with atomic ownership transfer (Rules 023, 035, 043)
                batch: List[CG01EvaluationTrace] = []
                receipts: List[Optional[QueueReceipt]] = []
                while len(batch) < 50:
                    try:
                        q_item = _TRACE_QUEUE.get_nowait()
                        if isinstance(q_item, tuple) and len(q_item) == 2 and isinstance(q_item[1], QueueReceipt):
                            t, r = q_item
                        elif isinstance(q_item, CG01EvaluationTrace):
                            t = q_item
                            r_id = f"rcpt_legacy_{t.trace_id}_{int(time.time()*1000)}_{uuid.uuid4().hex[:6]}"
                            r = QueueReceipt(receipt_id=r_id, created_at_ms=int(time.time()*1000), queue_ref=_TRACE_QUEUE)
                            with _RETRY_BUFFER_LOCK:
                                _ACTIVE_RECEIPTS[r_id] = r
                        else:
                            t = q_item
                            r = None
                        batch.append(t)
                        receipts.append(r)
                    except queue.Empty:
                        break

                if batch:
                    batch_id = f"batch_queue_{int(time.time()*1000)}_{len(batch)}_{uuid.uuid4().hex[:4]}"
                    handle = BatchHandle(
                        batch_id=batch_id,
                        trace_ids=[t.trace_id for t in batch],
                        traces=batch,
                        receipts=receipts,
                        attempt=1,
                        state="SUBMITTED",
                        is_retry=False,
                    )
                    with _RETRY_BUFFER_LOCK:
                        _IN_FLIGHT_COUNT += len(batch)
                        _ACTIVE_BATCH_HANDLES[batch_id] = handle

                    def _run_queue_sync(b_id=batch_id, b_handle=handle, b_batch=batch, b_rcpts=receipts):
                        return _persist_trace_batch_sync(b_batch, None, 1, True, b_handle, receipts=b_rcpts)

                    op_task = asyncio.create_task(asyncio.to_thread(_run_queue_sync))
                    handle.operation_task = op_task
                    with _RETRY_BUFFER_LOCK:
                        _ACTIVE_OPERATIONS[batch_id] = op_task

                    def _make_queue_done(b_id=batch_id):
                        def _on_done(t: asyncio.Task):
                            with _RETRY_BUFFER_LOCK:
                                _ACTIVE_OPERATIONS.pop(b_id, None)
                            if not t.cancelled():
                                exc = t.exception()
                                if exc:
                                    logger.error(f"Queue batch {b_id} task exception: {exc}")
                        return _on_done

                    op_task.add_done_callback(_make_queue_done(batch_id))

                    try:
                        await asyncio.shield(op_task)
                    except asyncio.CancelledError:
                        # V16.25 Rules 021-024, 031-050: Waiter cancelled while batch is submitted/running!
                        # Shielded op_task continues running/queued in executor and retains batch ownership.
                        raise
                    except Exception as be:
                        logger.error(f"Error persisting trace batch: {be}")

                await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Trace persistence worker loop error: {e}", exc_info=True)
                await asyncio.sleep(1.0)
    finally:
        with _RETRY_BUFFER_LOCK:
            _TRACE_WORKER_RUNNING = False
            _TRACE_WORKER_STATE = "STOPPED"
            _TRACE_WORKER_TASK = None


def stop_trace_persistence_worker():
    global _TRACE_WORKER_RUNNING, _TRACE_WORKER_STATE
    _TRACE_WORKER_RUNNING = False
    if _TRACE_WORKER_STATE == "RUNNING":
        _TRACE_WORKER_STATE = "STOPPING"


class CG01TraceRecorder:
    """
    Non-blocking, thread-safe in-memory ring buffer for CG-01 evaluation traces (Rule 040).
    Keeps telemetry strictly off the synchronous DB commit path, with persistent backing store.
    """
    def __init__(self, maxlen: int = 2000):
        self._lock = threading.Lock()
        self._buffer: collections.deque[CG01EvaluationTrace] = collections.deque(maxlen=maxlen)
        self._maxlen = maxlen
        self.dropped_traces_count = 0
        self.total_recorded_count = 0

    def record_trace(
        self,
        trace: CG01EvaluationTrace,
        persist: bool = True,
        db: Optional[Any] = None,
    ) -> None:
        """Enqueue trace into ring buffer; tracks drops if buffer fills; durably persists to DB."""
        # Immutable snapshot for ring buffer (Rule 037)
        snap = copy.deepcopy(trace)
        with self._lock:
            if len(self._buffer) >= self._maxlen:
                self.dropped_traces_count += 1
            self._buffer.append(snap)
            self.total_recorded_count += 1

        if persist:
            if db is not None:
                # Caller provided DB: only flush in caller session, NEVER commit! (Rule 032)
                CG01TracePersistenceWriter.persist_trace(snap, db=db)
            else:
                # No DB provided: enqueue for background persistence or write with dedicated session
                if _TRACE_WORKER_RUNNING or _TRACE_WORKER_STATE in ("STARTING", "RUNNING", "STOPPING"):
                    enqueue_trace_persistence(snap)
                else:
                    CG01TracePersistenceWriter.persist_trace(snap, db=None)

    def get_traces(
        self,
        symbol: Optional[str] = None,
        run_id: Optional[str] = None,
        mode: Optional[str] = None,
        limit: int = 100,
    ) -> List[CG01EvaluationTrace]:
        """Fetch traces filtered by symbol, run_id, or mode, merging memory buffer with persistent DB."""
        with self._lock:
            items = list(self._buffer)

        seen_ids = set()
        res: List[CG01EvaluationTrace] = []

        for t in reversed(items):
            if symbol and t.symbol != symbol:
                continue
            if run_id and t.run_id != run_id:
                continue
            if mode and getattr(t, "observation_mode", None) != mode:
                continue
            seen_ids.add(t.trace_id)
            res.append(t)
            if len(res) >= limit:
                break

        # Query DB if run_id is requested or in-memory ring buffer has no traces (e.g. after restart)
        if run_id or len(self._buffer) == 0:
            db_limit = limit * 2 if limit else 200
            persisted = CG01TracePersistenceWriter.load_traces(
                symbol=symbol,
                run_id=run_id,
                mode=mode,
                limit=db_limit,
            )
            for pt in persisted:
                if pt.trace_id not in seen_ids:
                    seen_ids.add(pt.trace_id)
                    res.append(pt)
                if len(res) >= limit:
                    break

        # Sort by completed/started timestamp descending
        res.sort(key=lambda x: int(x.evaluation_completed_at or x.evaluation_started_at or 0), reverse=True)
        return res[:limit]

    def get_traces_by_mode(
        self,
        mode: str,
        symbol: Optional[str] = None,
        run_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[CG01EvaluationTrace]:
        """Explicit mode-filtered trace retrieval (Rule 029)."""
        return self.get_traces(symbol=symbol, run_id=run_id, mode=mode, limit=limit)

    def get_recent_traces(
        self,
        limit: int = 100,
        symbol: Optional[str] = None,
        run_id: Optional[str] = None,
        mode: Optional[str] = None,
    ) -> List[CG01EvaluationTrace]:
        """Alias for get_traces to satisfy coverage report callers (Rules 026, 121)."""
        return self.get_traces(symbol=symbol, run_id=run_id, mode=mode, limit=limit)

    def get_latest_trace(self, symbol: Optional[str] = None) -> Optional[CG01EvaluationTrace]:
        """Return the most recent recorded evaluation trace, or None if empty."""
        traces = self.get_traces(symbol=symbol, limit=1)
        return traces[0] if traces else None

    def get_latency_summary(
        self,
        symbol: Optional[str] = None,
        run_id: Optional[str] = None,
        mode: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Calculate p50, p95, p99 percentiles for all key delay stages (Rules 041-050, 128).
        """
        traces = self.get_traces(symbol=symbol, run_id=run_id, mode=mode, limit=self._maxlen)

        feed_delays: List[float] = []
        sched_delays: List[float] = []
        runtime_delays: List[float] = []
        arm_delays: List[float] = []
        fill_delays: List[float] = []

        for t in traces:
            delays = t.calculate_delays()
            if delays["feed_delay_ms"] is not None:
                feed_delays.append(delays["feed_delay_ms"])
            if delays["scheduler_delay_ms"] is not None:
                sched_delays.append(delays["scheduler_delay_ms"])
            if delays["detector_runtime_ms"] is not None:
                runtime_delays.append(delays["detector_runtime_ms"])
            if delays["arm_delay_ms"] is not None:
                arm_delays.append(delays["arm_delay_ms"])
            if delays["fill_delay_ms"] is not None:
                fill_delays.append(delays["fill_delay_ms"])

        def _calc_stats(values: List[float]) -> Dict[str, Optional[float]]:
            if not values:
                return {"count": 0, "p50": None, "p95": None, "p99": None, "mean": None, "max": None}
            s = sorted(values)
            n = len(s)
            p50 = s[int(n * 0.50)]
            p95 = s[min(int(n * 0.95), n - 1)]
            p99 = s[min(int(n * 0.99), n - 1)]
            return {
                "count": n,
                "p50": round(float(p50), 2),
                "p95": round(float(p95), 2),
                "p99": round(float(p99), 2),
                "mean": round(float(sum(s) / n), 2),
                "max": round(float(s[-1]), 2),
            }

        return {
            "sample_count": len(traces),
            "dropped_traces_count": self.dropped_traces_count,
            "feed_delay": _calc_stats(feed_delays),
            "scheduler_delay": _calc_stats(sched_delays),
            "detector_runtime": _calc_stats(runtime_delays),
            "arm_delay": _calc_stats(arm_delays),
            "fill_delay": _calc_stats(fill_delays),
        }

    def clear(self) -> None:
        with self._lock:
            self._buffer.clear()
            self.dropped_traces_count = 0
            self.total_recorded_count = 0


# Global singleton
cg01_trace_recorder = CG01TraceRecorder(maxlen=2000)

