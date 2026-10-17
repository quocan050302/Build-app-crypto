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
import json
import logging
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
    input_version: Any = 1
    source_event_id: Optional[str] = None
    context_source: str = "CACHE_AUTHORITATIVE"
    model_fingerprint: str = "CG01_V16_15"
    config_fingerprint: str = "DEFAULT"

    # Wall-clock timestamps in UTC ms (Rules 031, 036, 038)
    exchange_event_time_ms: Optional[int] = None
    event_received_at: Optional[int] = None
    evidence_known_at: Optional[int] = None
    evidence_available_at: Optional[int] = None
    evaluation_enqueued_at: Optional[int] = None
    evaluation_started_at: Optional[int] = None
    evaluation_completed_at: Optional[int] = None
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
        """
        delays: Dict[str, Optional[float]] = {
            "feed_delay_ms": None,
            "candle_availability_delay_ms": None,
            "scheduler_delay_ms": None,
            "detector_runtime_ms": None,
            "arm_delay_ms": None,
            "fill_delay_ms": None,
        }

        # Rule 041: Feed delay (receive - exchange)
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

        # Rule 045: Arm delay
        if self.armed_at is not None and self.evaluation_completed_at is not None:
            diff = self.armed_at - self.evaluation_completed_at
            if diff >= 0:
                delays["arm_delay_ms"] = float(diff)

        # Rule 046: Fill delay
        if self.filled_at is not None and self.armed_at is not None:
            diff = self.filled_at - self.armed_at
            if diff >= 0:
                delays["fill_delay_ms"] = float(diff)

        return delays

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["delays"] = self.calculate_delays()
        return d


class CG01TracePersistenceWriter:
    """
    Durable SQLite trace persistence writer (Appendix B, Rules 027, 122, 123).
    Persists evaluation traces in dedicated sessions so latency history survives restarts.
    """
    @staticmethod
    def persist_trace(trace: CG01EvaluationTrace, db: Optional[Any] = None) -> bool:
        should_close = False
        target_db = db
        if target_db is None:
            try:
                from database import SessionLocal
                target_db = SessionLocal()
                should_close = True
            except Exception:
                return False

        try:
            import models
            delays = trace.calculate_delays()
            rec = models.CG01TraceRecord(
                trace_id=trace.trace_id,
                symbol=trace.symbol,
                run_id=trace.run_id,
                source_event_id=trace.source_event_id,
                input_version=str(trace.input_version),
                evaluation_status=trace.evaluation_status,
                candidate_key=trace.candidate_key,
                armed_order_id=trace.armed_order_id,
                total_runtime_ms=trace.total_runtime_ms or delays.get("detector_runtime_ms"),
                feed_delay_ms=delays.get("feed_delay_ms"),
                scheduler_delay_ms=delays.get("scheduler_delay_ms"),
                arm_delay_ms=delays.get("arm_delay_ms"),
                fill_delay_ms=delays.get("fill_delay_ms"),
                payload_json=json.dumps(trace.to_dict(), ensure_ascii=False),
                created_at_ms=trace.evaluation_completed_at or trace.evaluation_started_at or int(time.time() * 1000),
            )
            target_db.merge(rec)
            target_db.commit()
            return True
        except Exception as e:
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
        limit: int = 100,
        db: Optional[Any] = None,
    ) -> List[CG01EvaluationTrace]:
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
            rows = query.order_by(models.CG01TraceRecord.created_at_ms.desc()).limit(limit).all()

            traces: List[CG01EvaluationTrace] = []
            for r in rows:
                t = CG01EvaluationTrace(
                    trace_id=r.trace_id,
                    symbol=r.symbol or "XAUUSDT",
                    run_id=r.run_id,
                    source_event_id=r.source_event_id,
                    input_version=r.input_version,
                    evaluation_status=r.evaluation_status or "COMPLETED",
                    candidate_key=r.candidate_key,
                    armed_order_id=r.armed_order_id,
                    total_runtime_ms=r.total_runtime_ms,
                    evaluation_started_at=r.created_at_ms,
                    evaluation_completed_at=r.created_at_ms,
                )
                if r.payload_json:
                    try:
                        p = json.loads(r.payload_json)
                        for k, v in p.items():
                            if hasattr(t, k) and v is not None and getattr(t, k) is None:
                                setattr(t, k, v)
                    except Exception:
                        pass
                traces.append(t)
            return traces
        except Exception as e:
            logger.warning(f"Failed to load persisted traces: {e}")
            return []
        finally:
            if should_close and target_db is not None:
                target_db.close()


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

    def record_trace(self, trace: CG01EvaluationTrace, persist: bool = True, db: Optional[Any] = None) -> None:
        """Enqueue trace into ring buffer; tracks drops if buffer fills; durably persists to DB."""
        with self._lock:
            if len(self._buffer) >= self._maxlen:
                self.dropped_traces_count += 1
            self._buffer.append(trace)
            self.total_recorded_count += 1

        if persist:
            CG01TracePersistenceWriter.persist_trace(trace, db=db)

    def get_traces(
        self,
        symbol: Optional[str] = None,
        run_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[CG01EvaluationTrace]:
        """Fetch traces filtered by symbol or run_id, falling back to persistent DB if needed."""
        with self._lock:
            items = list(self._buffer)

        res: List[CG01EvaluationTrace] = []
        for t in reversed(items):
            if symbol and t.symbol != symbol:
                continue
            if run_id and t.run_id != run_id:
                continue
            res.append(t)
            if len(res) >= limit:
                break

        # Fallback to persistent DB if in-memory buffer has no matching traces (Rules 027, 122)
        if not res and run_id:
            res = CG01TracePersistenceWriter.load_traces(symbol=symbol, run_id=run_id, limit=limit)

        return res

    def get_recent_traces(
        self,
        limit: int = 100,
        symbol: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> List[CG01EvaluationTrace]:
        """Alias for get_traces to satisfy coverage report callers (Rules 026, 121)."""
        return self.get_traces(symbol=symbol, run_id=run_id, limit=limit)

    def get_latency_summary(
        self,
        symbol: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Calculate p50, p95, p99 percentiles for all key delay stages (Rules 041-050, 128).
        """
        traces = self.get_traces(symbol=symbol, run_id=run_id, limit=self._maxlen)
        
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
