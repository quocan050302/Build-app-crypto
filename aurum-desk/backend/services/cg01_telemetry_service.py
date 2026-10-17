"""
backend/services/cg01_telemetry_service.py
V16.14 - CG-01 Latency Telemetry, Span Timing, and Trace Recording.

Enforces:
- Rules 031-040: Time contract, monotonic span duration, non-blocking thread-safe ring buffer.
- Rules 041-050: Percentile latency calculation (p50, p95, p99) without hiding tail latency.
- Rules 128: Per-stage and end-to-end breakdown.
"""
from __future__ import annotations

import collections
import copy
import json
import logging
import queue
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence

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

        # Rule 041 & 100: Feed delay (receive - exchange)
        if self.event_received_at is not None and self.exchange_event_time_ms is not None:
            diff = self.event_received_at - self.exchange_event_time_ms
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


import queue

_TRACE_QUEUE: queue.Queue = queue.Queue(maxsize=10000)
_TRACE_QUEUE_DROPS = 0
_TRACE_PERSIST_FAILURES = 0
_TRACE_PERSISTED_COUNT = 0
_TRACE_WORKER_RUNNING = False
_TRACE_WORKER_THREAD: Optional[threading.Thread] = None

# V16.20 Rules 081-087: In-flight and retry buffer tracking
_IN_FLIGHT_COUNT = 0
_RETRY_BUFFER: List[Tuple[CG01EvaluationTrace, int]] = []  # (trace, attempt_count)
_RETRY_BUFFER_LOCK = threading.Lock()


def get_in_flight_count() -> int:
    return _IN_FLIGHT_COUNT


def set_in_flight_count(val: int) -> None:
    global _IN_FLIGHT_COUNT
    _IN_FLIGHT_COUNT = max(0, val)


def get_retry_buffer_count() -> int:
    with _RETRY_BUFFER_LOCK:
        return len(_RETRY_BUFFER)


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
                # Dedicated session: safe to commit
                target_db.commit()
            else:
                # Caller's session: ONLY flush, NEVER commit! (Rule 031, 032)
                target_db.flush()

            _TRACE_PERSISTED_COUNT += 1
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
    Appendix B, Rule 033: Non-blocking enqueue of immutable trace snapshot for background persistence.
    """
    global _TRACE_QUEUE_DROPS
    try:
        # Enqueue deep copy / snapshot
        t_copy = copy.deepcopy(trace)
        _TRACE_QUEUE.put_nowait(t_copy)
        return True
    except queue.Full:
        _TRACE_QUEUE_DROPS += 1
        logger.warning("Trace persistence queue full; dropped trace")
        return False
    except Exception as e:
        logger.warning(f"Failed to enqueue trace persistence: {e}")
        return False


def flush_pending_traces(timeout_s: float = 5.0) -> Dict[str, Any]:
    """
    Appendix B, Rules 026, 064, 065, 087, 101, 146: Flush all pending traces within timeout.
    Truthfully accounts for succeeded vs failed writes, and tracks in-flight and retrying writes.
    Does NOT report COMPLETED prematurely if an in-flight batch is committing.
    """
    start_t = time.time()
    flushed = 0
    failed = 0

    # 1. Drain existing retry buffer if any
    with _RETRY_BUFFER_LOCK:
        retries = list(_RETRY_BUFFER)
        _RETRY_BUFFER.clear()

    for trace, attempt in retries:
        res = _persist_trace_batch_sync([trace], attempt=attempt)
        flushed += res.committed_count
        failed += res.failed_count

    # 2. Drain in-queue items
    while not _TRACE_QUEUE.empty() and (time.time() - start_t) < timeout_s:
        try:
            t = _TRACE_QUEUE.get_nowait()
        except queue.Empty:
            break

        try:
            res = _persist_trace_batch_sync([t])
            flushed += res.committed_count
            failed += res.failed_count
        except Exception as e:
            failed += 1
            logger.warning(f"Error during trace flush: {e}")

    # Wait for in-flight writes to finish or timeout
    while _IN_FLIGHT_COUNT > 0 and (time.time() - start_t) < timeout_s:
        time.sleep(0.05)

    pending = _TRACE_QUEUE.qsize()
    retry_pending = get_retry_buffer_count()
    current_inflight = _IN_FLIGHT_COUNT

    if failed > 0:
        status = "FAILED"
    elif pending == 0 and current_inflight == 0 and retry_pending == 0:
        status = "COMPLETED"
    elif current_inflight > 0:
        status = "TIMED_OUT" if (time.time() - start_t) >= timeout_s else "IN_FLIGHT"
    else:
        status = "TIMED_OUT"

    return {
        "flushed": flushed,
        "failed": failed,
        "pending": pending,
        "in_flight": current_inflight,
        "retry_buffer": retry_pending,
        "status": status,
        "elapsed_sec": round(time.time() - start_t, 3),
    }


def _persist_trace_batch_sync(
    batch: List[CG01EvaluationTrace],
    session_factory: Optional[Any] = None,
    attempt: int = 1,
) -> TraceBatchResult:
    """
    Rules 056-060, 081-085, 087: Persist trace batch in worker-owned session,
    committing only upon complete batch success.
    On failure, retains batch in _RETRY_BUFFER (up to 3 attempts) and does NOT call task_done().
    Returns structured TraceBatchResult.
    """
    global _TRACE_PERSISTED_COUNT, _TRACE_PERSIST_FAILURES, _IN_FLIGHT_COUNT
    with _RETRY_BUFFER_LOCK:
        _IN_FLIGHT_COUNT += len(batch)

    from database import SessionLocal
    sf = session_factory or SessionLocal
    db = sf()
    persisted = 0
    failed = 0
    batch_ids = [t.trace_id for t in batch]

    try:
        for t in batch:
            ok = CG01TracePersistenceWriter.persist_trace(t, db=db)
            if ok:
                persisted += 1
            else:
                failed += 1
        db.commit()

        # ACK ONLY AFTER SUCCESSFUL COMMIT! (Rule 058, 083)
        _TRACE_PERSISTED_COUNT += persisted
        for _ in range(len(batch)):
            try:
                _TRACE_QUEUE.task_done()
            except ValueError:
                pass

        if failed == 0:
            batch_status = "COMMITTED"
        elif persisted == 0:
            batch_status = "PERMANENT_FAILURE"
        else:
            batch_status = "PARTIAL_REJECTED"

        return TraceBatchResult(
            status=batch_status,
            committed_count=persisted,
            failed_count=failed,
            retained_for_retry_count=0,
            batch_ids=batch_ids,
        )
    except Exception as be:
        try:
            if hasattr(db, "rollback"):
                db.rollback()
        except Exception:
            pass
        logger.warning(f"Error persisting trace batch (attempt {attempt}): {be}")
        _TRACE_PERSIST_FAILURES += len(batch)

        retained = 0
        if attempt < 3:
            # Retain batch for retry! DO NOT call task_done()
            with _RETRY_BUFFER_LOCK:
                for t in batch:
                    _RETRY_BUFFER.append((t, attempt + 1))
                    retained += 1
            status = "RETRYABLE_FAILURE"
        else:
            # Dead-letter / dropped after max retries
            for _ in range(len(batch)):
                try:
                    _TRACE_QUEUE.task_done()
                except ValueError:
                    pass
            status = "PERMANENT_FAILURE"

        return TraceBatchResult(
            status=status,
            committed_count=0,
            failed_count=len(batch),
            retained_for_retry_count=retained,
            error=str(be),
            batch_ids=batch_ids,
        )
    finally:
        with _RETRY_BUFFER_LOCK:
            _IN_FLIGHT_COUNT = max(0, _IN_FLIGHT_COUNT - len(batch))
        if hasattr(db, "close"):
            db.close()


async def run_trace_persistence_worker() -> None:
    """
    Appendix B, Rules 035, 038, 056-060, 081-085: Background worker draining trace queue with isolated DB sessions.
    Executes synchronous database operations in thread pool without blocking asyncio event loop.
    Prioritizes and retries batches in _RETRY_BUFFER.
    """
    global _TRACE_WORKER_RUNNING
    _TRACE_WORKER_RUNNING = True
    logger.info("CG01 trace persistence worker started.")
    while _TRACE_WORKER_RUNNING:
        try:
            # 1. Drain retries first
            retry_items: List[Tuple[CG01EvaluationTrace, int]] = []
            with _RETRY_BUFFER_LOCK:
                if _RETRY_BUFFER:
                    retry_items = _RETRY_BUFFER[:10]
                    del _RETRY_BUFFER[:len(retry_items)]

            for trace, attempt in retry_items:
                await asyncio.to_thread(_persist_trace_batch_sync, [trace], None, attempt)

            # 2. Drain new items from queue
            batch: List[CG01EvaluationTrace] = []
            while len(batch) < 50:
                try:
                    item = _TRACE_QUEUE.get_nowait()
                    batch.append(item)
                except queue.Empty:
                    break

            if batch:
                await asyncio.to_thread(_persist_trace_batch_sync, batch)

            await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            _TRACE_WORKER_RUNNING = False
            break
        except Exception as e:
            logger.error(f"Trace persistence worker loop error: {e}", exc_info=True)
            await asyncio.sleep(1.0)


def stop_trace_persistence_worker():
    global _TRACE_WORKER_RUNNING
    _TRACE_WORKER_RUNNING = False


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
                if _TRACE_WORKER_RUNNING:
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

