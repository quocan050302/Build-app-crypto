"""
backend/services/cg01_scheduler_service.py
V16.14 - Event-Driven LTF Evaluation Scheduler for CG-01.

Enforces:
- Rules 051-060: Single-flight per symbol, immutable input bundle, event-driven closed-candle triggers.
- Rules 061-070: Authoritative cache reading without waiting for DB persistence; immutability from WS feed mutations.
- Rules 161-165: Independent feature flags, shadow comparison before ownership, watchdog coexistence.
- Appendix A #1, #2, #3: notify_market_change, run, capture_cg01_input_bundle.
"""
from __future__ import annotations

import asyncio
import collections
import copy
import logging
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple
from sqlalchemy.orm import Session

from database import SessionLocal
import schemas
from services.cg01_telemetry_service import (
    CG01EvaluationTrace,
    CG01Span,
    cg01_trace_recorder,
)
from services.cg01_types import CG01Config

logger = logging.getLogger(__name__)

# Map timeframes to standard bar duration in milliseconds
TF_DURATIONS_MS = {
    "1M": 60 * 1000,
    "5M": 5 * 60 * 1000,
    "15M": 15 * 60 * 1000,
    "1H": 60 * 60 * 1000,
    "4H": 4 * 60 * 60 * 1000,
    "D": 24 * 60 * 60 * 1000,
}


def capture_cg01_input_bundle(
    symbol: str = "XAUUSDT",
    as_of_ms: Optional[int] = None,
    config: Optional[CG01Config] = None,
) -> Dict[str, Any]:
    """
    Appendix A #3 & Rules 061-064:
    Capture an immutable, causal snapshot of authoritative closed candles from CandleCacheService.
    Does NOT block on DB persistence worker (Rule 064).
    Guarantees strict immutability by deepcopying candle models before returning to worker threads (Rule 062).
    """
    from services.candle_cache_service import candle_cache_service

    now_ms = as_of_ms if as_of_ms is not None else int(time.time() * 1000)
    bundle: Dict[str, Any] = {
        "symbol": symbol,
        "as_of_ms": now_ms,
        "context_source": "CACHE_AUTHORITATIVE",
        "input_versions": {},
        "last_closed_times": {},
        "candle_revision_metadata": {},
        "4H": [],
        "1H": [],
        "15M": [],
        "5M": [],
        "1M": [],
    }

    for tf in ["4H", "1H", "15M", "5M", "1M"]:
        cached = candle_cache_service.get_candles(symbol, tf, limit=150)
        dur = TF_DURATIONS_MS.get(tf, 60000)

        closed_immutable: List[schemas.CandleCreate] = []
        last_closed_ts: Optional[int] = None

        for c in cached:
            # Rule 024 & causality: must be closed and closed on/before now_ms
            c_close_ms = c.timestamp + dur
            if getattr(c, "is_closed", False) and c_close_ms <= now_ms:
                # Deep copy to isolate from concurrent WS stream mutation
                closed_immutable.append(
                    schemas.CandleCreate(
                        symbol=c.symbol,
                        timeframe=c.timeframe,
                        timestamp=c.timestamp,
                        open=float(c.open),
                        high=float(c.high),
                        low=float(c.low),
                        close=float(c.close),
                        volume=float(c.volume),
                        is_closed=True,
                    )
                )
                last_closed_ts = c.timestamp

        bundle[tf] = closed_immutable
        bundle["input_versions"][tf] = len(closed_immutable)
        bundle["last_closed_times"][tf] = last_closed_ts

    return bundle


class CG01EvaluationScheduler:
    """
    Event-driven evaluation scheduler for CG-01 LTF (Rules 051-060, 161-165).
    - Receives normalized market change events from candle feed.
    - Manages single-flight execution per symbol.
    - Prevents duplicate evaluations on duplicate WS events.
    - Operates in shadow mode by default (telemetry & comparison without production arming).
    - Can be promoted to execution owner, turning periodic watcher into a watchdog.
    """
    def __init__(self):
        self._lock = threading.RLock()
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._wakeup_event: Optional[asyncio.Event] = None

        # Feature flags (Rules 161, 162, 165)
        self.enabled: bool = True
        self.shadow_mode: bool = True       # Shadow evaluation by default (does not execute live orders)
        self.is_owner: bool = False         # False = periodic watcher is owner; True = scheduler is execution owner

        # State tracking per symbol
        self._dirty_symbols: Set[str] = set()
        self._symbol_in_flight: Set[str] = set()
        self._seen_events: collections.deque[str] = collections.deque(maxlen=2000)
        self._last_evaluated_event_time: Dict[str, int] = {}
        self._pending_market_events: Dict[str, Dict[str, Any]] = {}

        # Telemetry counters
        self.events_received_count = 0
        self.evaluations_scheduled_count = 0
        self.duplicate_events_ignored_count = 0
        self.dropped_evaluations_count = 0

    def set_flags(
        self,
        *,
        enabled: Optional[bool] = None,
        shadow_mode: Optional[bool] = None,
        is_owner: Optional[bool] = None,
    ) -> None:
        """Update scheduler operational mode cleanly (Rule 161, 166)."""
        with self._lock:
            if enabled is not None:
                self.enabled = enabled
            if shadow_mode is not None:
                self.shadow_mode = shadow_mode
            if is_owner is not None:
                self.is_owner = is_owner
        logger.info(
            f"CG01EvaluationScheduler flags updated: enabled={self.enabled}, "
            f"shadow_mode={self.shadow_mode}, is_owner={self.is_owner}"
        )

    def notify_market_change(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """
        Appendix A #1: Receive normalized candle / data change event.
        Marks symbol dirty and signals the scheduler loop.
        Fast, non-blocking: NO synchronous evaluation, NO HTTP, NO DB writes here.
        """
        symbol = str(event.get("symbol") or "XAUUSDT")
        tf = str(event.get("timeframe") or "1M")
        event_type = str(event.get("event_type") or "CANDLE_CLOSED")
        candle_ts = int(event.get("candle_timestamp") or 0)
        revision = int(event.get("revision") or 0)
        received_at = int(event.get("received_at_ms") or int(time.time() * 1000))

        # Rule 056: Event identity and deduplication
        event_id = f"{symbol}:{tf}:{candle_ts}:{revision}:{event_type}"
        with self._lock:
            if event_id in self._seen_events:
                self.duplicate_events_ignored_count += 1
                return {"enqueued": False, "reason": "DUPLICATE_EVENT", "event_id": event_id}

            self._seen_events.append(event_id)
            self.events_received_count += 1

            if not self.enabled:
                return {"enqueued": False, "reason": "SCHEDULER_DISABLED", "event_id": event_id}

            # Only closed candles or significant structural events trigger evaluation (Rules 053, 054)
            if event_type == "CANDLE_CLOSED":
                self._dirty_symbols.add(symbol)
                self._pending_market_events[symbol] = {
                    "event_id": event_id,
                    "event_type": event_type,
                    "timeframe": tf,
                    "candle_timestamp": candle_ts,
                    "exchange_event_time_ms": event.get("exchange_event_time_ms"),
                    "received_at_ms": received_at,
                    "enqueued_at_ms": int(time.time() * 1000),
                }

        # Wake up async loop if active
        if self._wakeup_event is not None and not self._wakeup_event.is_set():
            try:
                loop = asyncio.get_running_loop()
                loop.call_soon_threadsafe(self._wakeup_event.set)
            except RuntimeError:
                pass

        return {"enqueued": True, "reason": "DIRTY_MARKED", "event_id": event_id}

    def evaluate_symbol_now(
        self,
        db: Session,
        symbol: str = "XAUUSDT",
        *,
        as_of_ms: Optional[int] = None,
        source_event: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Synchronously execute an event-driven evaluation cycle for a symbol with tracing.
        Respects single-flight per symbol (Rule 057).
        """
        from services.scalp_watcher_orchestrator import scalp_watcher_orchestrator

        with self._lock:
            if symbol in self._symbol_in_flight:
                # Another evaluation is currently in flight for this symbol
                self._dirty_symbols.add(symbol)  # Ensure re-evaluates once current finishes
                return {"status": "DEFERRED_IN_FLIGHT", "symbol": symbol}
            self._symbol_in_flight.add(symbol)
            self._dirty_symbols.discard(symbol)

        t_now = as_of_ms if as_of_ms is not None else int(time.time() * 1000)
        trace_id = f"trc_{symbol}_{t_now}_{int(time.perf_counter() * 1000) % 100000}"

        ev = source_event or {}
        trace = CG01EvaluationTrace(
            trace_id=trace_id,
            symbol=symbol,
            source_event_id=ev.get("event_id"),
            exchange_event_time_ms=ev.get("exchange_event_time_ms"),
            event_received_at=ev.get("received_at_ms"),
            evidence_available_at=ev.get("received_at_ms"),
            evaluation_enqueued_at=ev.get("enqueued_at_ms"),
            evaluation_started_at=t_now,
        )

        p_start = time.perf_counter()
        try:
            # Capture immutable input bundle (Rule 062, Appendix A #3)
            bundle = capture_cg01_input_bundle(symbol=symbol, as_of_ms=t_now)
            trace.input_version = bundle["input_versions"].get("1M", 1)

            # Auto execution control: in shadow mode, we force auto_enabled=False so no real orders arm
            effective_auto = False if self.shadow_mode else (True if self.is_owner else None)

            # Delegate to canonical production evaluate_cycle (Rule 052)
            span_engine = CG01Span(name="engine_evaluation", start_perf=time.perf_counter())
            cycle_result = scalp_watcher_orchestrator.evaluate_cycle(
                db=db,
                symbol=symbol,
                now_ms=t_now,
                candles_1m=bundle.get("1M"),
                candles_5m=bundle.get("5M"),
                candles_15m=bundle.get("15M"),
                candles_1h=bundle.get("1H"),
                candles_4h=bundle.get("4H"),
                auto_enabled=effective_auto,
            )
            span_engine.finish()
            trace.total_runtime_ms = span_engine.duration_ms

            t_done = int(time.time() * 1000)
            trace.evaluation_completed_at = t_done

            # Extract outcome details
            cg_eval = cycle_result.get("cg01_evaluation") or {}
            trace.candidate_key = cg_eval.get("candidate_key")
            trace.armed_order_id = cycle_result.get("armed_order_id")
            if trace.armed_order_id:
                trace.armed_at = t_done

            trace.evaluation_status = cycle_result.get("status", "COMPLETED")
            trace.skip_reason = cg_eval.get("skip_reason")

            # Record telemetry trace in ring buffer (Rule 040)
            cg01_trace_recorder.record_trace(trace)
            self.evaluations_scheduled_count += 1

            return {
                "status": "COMPLETED",
                "symbol": symbol,
                "trace_id": trace_id,
                "shadow_mode": self.shadow_mode,
                "is_owner": self.is_owner,
                "armed_order_id": trace.armed_order_id,
                "runtime_ms": trace.total_runtime_ms,
                "cycle_result": cycle_result,
            }

        except Exception as e:
            logger.error(f"Error during event-driven evaluation for {symbol}: {e}", exc_info=True)
            trace.evaluation_status = "ERROR"
            trace.skip_reason = str(e)
            trace.evaluation_completed_at = int(time.time() * 1000)
            cg01_trace_recorder.record_trace(trace)
            return {"status": "ERROR", "symbol": symbol, "error": str(e)}

        finally:
            with self._lock:
                self._symbol_in_flight.discard(symbol)

    async def run(self) -> None:
        """
        Appendix A #2: Long-running async scheduler worker.
        Drains dirty symbols under single-flight control.
        """
        self._running = True
        self._wakeup_event = asyncio.Event()
        logger.info("CG01EvaluationScheduler run loop started.")

        while self._running:
            try:
                # Wait for market change or 5s safety poll
                try:
                    await asyncio.wait_for(self._wakeup_event.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    pass
                self._wakeup_event.clear()

                if not self.enabled:
                    continue

                # Collect symbols needing evaluation
                with self._lock:
                    symbols_to_process = list(self._dirty_symbols)

                for sym in symbols_to_process:
                    if not self._running:
                        break

                    with self._lock:
                        source_ev = self._pending_market_events.pop(sym, None)

                    def _do_eval(s=sym, ev=source_ev):
                        db = SessionLocal()
                        try:
                            return self.evaluate_symbol_now(db, s, source_event=ev)
                        finally:
                            db.close()

                    await asyncio.to_thread(_do_eval)

            except asyncio.CancelledError:
                self._running = False
                break
            except Exception as e:
                logger.error(f"CG01EvaluationScheduler loop error: {e}", exc_info=True)
                await asyncio.sleep(1.0)

    def stop(self) -> None:
        self._running = False
        if self._wakeup_event is not None:
            self._wakeup_event.set()


# Global singleton
cg01_evaluation_scheduler = CG01EvaluationScheduler()
