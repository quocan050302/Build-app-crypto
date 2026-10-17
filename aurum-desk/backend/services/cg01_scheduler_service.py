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
import hashlib
import json
from typing import Any, Dict, List, Optional, Set, Tuple
from sqlalchemy.orm import Session

from database import SessionLocal
import models
import schemas
from services.cg01_telemetry_service import (
    CG01EvaluationTrace,
    CG01Span,
    cg01_trace_recorder,
)
from services.cg01_types import CG01Config, CG01ProfileMode

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
        # Rule 061, 062: Real versioning per timeframe using last closed timestamp and count
        bundle["input_versions"][tf] = f"{tf}:{last_closed_ts}:{len(closed_immutable)}" if last_closed_ts else f"{tf}:EMPTY"
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
        """Update scheduler operational mode cleanly with validation (Rules 044, 161, 166)."""
        with self._lock:
            new_enabled = enabled if enabled is not None else self.enabled
            new_shadow = shadow_mode if shadow_mode is not None else self.shadow_mode
            new_owner = is_owner if is_owner is not None else self.is_owner

            # Rule 044: Validate flag combinations
            # If scheduler is disabled, it cannot be active execution owner
            if not new_enabled and new_owner:
                raise ValueError("Cannot set is_owner=True when scheduler is disabled")

            self.enabled = new_enabled
            self.shadow_mode = new_shadow
            self.is_owner = new_owner

        logger.info(
            f"CG01EvaluationScheduler flags updated: enabled={self.enabled}, "
            f"shadow_mode={self.shadow_mode}, is_owner={self.is_owner}"
        )

    def get_health_snapshot(self) -> Dict[str, Any]:
        """Rules 034, 035: Return granular operational health snapshot."""
        with self._lock:
            if not self.enabled or getattr(self, "_stopped", False):
                state = "STOPPED"
            elif self._running:
                state = "RUNNING"
            else:
                state = "CONFIGURED"
            return {
                "status": state,
                "running": self._running,
                "enabled": self.enabled,
                "shadow_mode": self.shadow_mode,
                "is_owner": self.is_owner,
                "effective_owner": "SCHEDULER" if (self.enabled and self.is_owner and self._running and not self.shadow_mode) else "WATCHER",
                "role": "OWNER" if (self.enabled and self.is_owner and self._running and not self.shadow_mode) else ("WATCHDOG" if (self.enabled and self._running) else "STANDBY"),
                "dirty_symbols": list(self._dirty_symbols),
                "in_flight_symbols": list(self._symbol_in_flight),
                "events_received_count": self.events_received_count,
                "evaluations_scheduled_count": self.evaluations_scheduled_count,
                "duplicate_events_ignored_count": self.duplicate_events_ignored_count,
            }

    def is_running(self) -> bool:
        """Return True if background worker loop is active."""
        with self._lock:
            return self._running

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
                # Rule 068: Preserve earliest arrival timestamp among pending events
                existing = self._pending_market_events.get(symbol)
                earliest_recv = existing.get("received_at_ms") if existing else received_at
                self._pending_market_events[symbol] = {
                    "event_id": event_id,
                    "event_type": event_type,
                    "timeframe": tf,
                    "candle_timestamp": candle_ts,
                    "exchange_event_time_ms": event.get("exchange_event_time_ms"),
                    "received_at_ms": earliest_recv,
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
        db_or_symbol: Any = "XAUUSDT",
        symbol_or_db: Any = None,
        *,
        symbol: Optional[str] = None,
        db: Optional[Session] = None,
        as_of_ms: Optional[int] = None,
        source_event: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Synchronously execute an event-driven evaluation cycle for a symbol with tracing.
        Respects single-flight per symbol (Rule 057).
        Supports either evaluate_symbol_now(db, symbol) or evaluate_symbol_now(symbol, db=db).
        """
        target_db = db
        target_symbol = symbol

        if isinstance(db_or_symbol, str):
            target_symbol = db_or_symbol
            if symbol_or_db is not None and not isinstance(symbol_or_db, str):
                target_db = symbol_or_db
        elif db_or_symbol is not None:
            target_db = db_or_symbol
            if isinstance(symbol_or_db, str):
                target_symbol = symbol_or_db

        if target_symbol is None:
            target_symbol = "XAUUSDT"

        symbol = target_symbol
        db = target_db

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

            # Resolve active research run and build immutable research context
            from services.paper_research_run_service import (
                resolve_active_observation_run,
                associate_order_with_research_run,
                record_committed_order_milestone,
                PaperResearchRunService,
            )
            from services.cg01_types import CG01ResearchContext

            act_run = resolve_active_observation_run(db=db, scope="CG01", symbol=symbol)
            if not act_run:
                act_run = PaperResearchRunService.get_or_create_run(symbol=symbol, scope="CG01", db=db)
            if act_run:
                trace.run_id = act_run.run_id

            obs_mode = "SHADOW" if self.shadow_mode else "ACTIVE"
            trace.observation_mode = obs_mode
            research_ctx = CG01ResearchContext(
                trace_id=trace_id,
                run_id=act_run.run_id if act_run else None,
                segment_id=None,
                symbol=symbol,
                config_fingerprint=None,
                input_versions=bundle.get("input_versions", {}),
                evaluation_as_of_ms=t_now,
                observation_mode=obs_mode,
            )

            # Auto execution control (Rules 041, 042):
            # In shadow mode or if scheduler is not owner, force auto_enabled=False.
            # When scheduler is owner, pass None so evaluate_cycle reads global auto from strategy_service!
            effective_auto = False if (self.shadow_mode or not self.is_owner) else None

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
                research_context=research_ctx,
            )
            span_engine.finish()
            trace.total_runtime_ms = span_engine.duration_ms

            t_done = int(time.time() * 1000)
            trace.evaluation_completed_at = t_done

            cg_eval = cycle_result.get("cg01_evaluation") or {}
            trace.candidate_key = cg_eval.get("candidate_key")
            trace.armed_order_id = cycle_result.get("armed_order_id")
            trace.evaluation_status = cycle_result.get("status", "COMPLETED")
            trace.skip_reason = cg_eval.get("skip_reason")

            if trace.armed_order_id:
                armed_ord = db.query(models.PaperOrder).filter(models.PaperOrder.id == trace.armed_order_id).first()
                if armed_ord and armed_ord.armed_at:
                    trace.armed_at = armed_ord.armed_at
                else:
                    trace.armed_at = t_done

                # Rules 022, 031, 032: Ensure order is linked and committed if not already linked by cycle
                existing_link = db.query(models.PaperResearchOrderLink).filter(
                    models.PaperResearchOrderLink.order_id == trace.armed_order_id
                ).first()
                if not existing_link and research_ctx and research_ctx.run_id:
                    try:
                        as_of = trace.armed_at or trace.evaluation_started_at or t_done
                        associate_order_with_research_run(
                            db,
                            run_id=research_ctx.run_id,
                            order_id=trace.armed_order_id,
                            trace_id=trace_id,
                            now_ms=as_of,
                        )
                        record_committed_order_milestone(
                            db,
                            run_id=research_ctx.run_id,
                            order_id=trace.armed_order_id,
                            milestone="ARMED",
                            timestamp_ms=as_of,
                            payload={"source": "CG01Scheduler", "trace_id": trace_id},
                        )
                        db.commit()
                    except Exception as link_err:
                        logger.warning(f"Error persisting fallback order research link: {link_err}")
                        db.rollback()

            # Record telemetry trace in ring buffer and durable store
            cg01_trace_recorder.record_trace(trace, db=db)
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
        Appendix A #2 & Rules 031-036: Long-running async scheduler worker.
        Drains dirty symbols under single-flight control.
        """
        with self._lock:
            if self._running:
                logger.warning("CG01EvaluationScheduler is already running; skipping duplicate start.")
                return
            self._running = True
            self._stopped = False
            try:
                self._task = asyncio.current_task()
            except RuntimeError:
                self._task = None

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
        self._stopped = True
        if self._wakeup_event is not None:
            self._wakeup_event.set()


# Global singleton
cg01_evaluation_scheduler = CG01EvaluationScheduler()


def resolve_entry_execution_owner(
    db_or_sched: Any = None,
    sched_or_db: Any = None,
    *,
    db: Optional[Session] = None,
    scheduler: Optional[CG01EvaluationScheduler] = None,
) -> Dict[str, Any]:
    """
    Rule 046 & Appendix B: Resolve whether SCHEDULER or WATCHER has active entry execution ownership.
    Takes into account worker health, feature flags, and global auto state.
    """
    target_db = db
    target_sched = scheduler

    if isinstance(db_or_sched, CG01EvaluationScheduler):
        target_sched = db_or_sched
        if sched_or_db is not None and not isinstance(sched_or_db, CG01EvaluationScheduler):
            target_db = sched_or_db
    elif db_or_sched is not None:
        target_db = db_or_sched
        if isinstance(sched_or_db, CG01EvaluationScheduler):
            target_sched = sched_or_db

    sched = target_sched or cg01_evaluation_scheduler
    health = sched.get_health_snapshot()

    global_auto = False
    if target_db is not None:
        try:
            from services.strategy_service import strategy_service
            global_auto = strategy_service.get_auto_state(target_db)
        except Exception:
            global_auto = False
    else:
        try:
            from database import SessionLocal
            from services.strategy_service import strategy_service
            local_db = SessionLocal()
            try:
                global_auto = strategy_service.get_auto_state(local_db)
            finally:
                local_db.close()
        except Exception:
            global_auto = False

    # Effective owner logic (Rules 041-047)
    if sched.enabled and sched.is_owner and sched._running and not sched.shadow_mode:
        owner = "SCHEDULER"
        watchdog = "WATCHER"
        can_execute = global_auto
        reason = "SCHEDULER_ACTIVE_OWNER"
    else:
        owner = "WATCHER"
        watchdog = "SCHEDULER" if (sched.enabled and sched._running) else "NONE"
        can_execute = global_auto
        if sched.shadow_mode:
            reason = "SCHEDULER_IN_SHADOW_MODE"
        elif not sched.is_owner:
            reason = "WATCHER_DEFAULT_OWNER"
        elif not sched._running:
            reason = "SCHEDULER_NOT_RUNNING"
        else:
            reason = "SCHEDULER_DISABLED"

    return {
        "execution_owner": owner,
        "watchdog": watchdog,
        "can_execute": can_execute,
        "global_auto": global_auto,
        "reason": reason,
        "scheduler_health": health,
    }


def resolve_effective_cg01_config(
    db: Optional[Session] = None,
    symbol: str = "XAUUSDT",
    base_config: Optional[CG01Config] = None,
) -> Tuple[CG01Config, str, str]:
    """
    Appendix B & Rules 071-073: Resolve effective CG01 config, content fingerprint, and provenance source.
    """
    source = "DEFAULT"
    cfg = base_config
    if cfg is None:
        if db is not None:
            try:
                from services.trading_policy_service import resolve_effective_daily_policy
                eff_daily = resolve_effective_daily_policy(db, "default")
                cfg = CG01Config(
                    enabled=True,
                    profile_mode=CG01ProfileMode.PAPER_EXECUTION.value,
                    daily_min_goal=eff_daily.min_goal,
                    daily_max_cap=eff_daily.max_fills,
                    daily_ny_goal=eff_daily.ny_goal,
                )
                source = "DATABASE_POLICY"
            except Exception:
                cfg = CG01Config()
                source = "FALLBACK_DEFAULT"
        else:
            cfg = CG01Config()
            source = "FALLBACK_DEFAULT"
    else:
        source = "EXPLICIT_PARAMETER"

    # Compute content fingerprint
    fingerprint_payload = {
        "model_version": cfg.model_version,
        "entry_mode": cfg.entry_mode,
        "min_net_rr": cfg.min_net_rr,
        "displacement_body_atr_min": cfg.displacement_body_atr_min,
        "pivot_left_bars": cfg.pivot_left_bars,
        "pivot_right_bars": cfg.pivot_right_bars,
        "timing_profile": cfg.timing_profile,
        "daily_min_goal": cfg.daily_min_goal,
        "daily_max_cap": cfg.daily_max_cap,
    }
    raw = json.dumps(fingerprint_payload, sort_keys=True)
    fingerprint = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    return cfg, fingerprint, source
