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
    "1D": 24 * 60 * 60 * 1000,
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
        "1D": [],
    }

    for tf in ["4H", "1H", "15M", "5M", "1M", "1D"]:
        cached = candle_cache_service.get_candles(symbol, tf, limit=150)
        if not cached and tf == "1D":
            cached = candle_cache_service.get_candles(symbol, "D", limit=150)
        dur = TF_DURATIONS_MS.get(tf, 60000)

        closed_immutable: List[schemas.CandleCreate] = []
        last_closed_ts: Optional[int] = None

        for c in cached:
            # Rule 024 & causality: must be closed and closed on/before now_ms
            c_ts = int(c.get("timestamp") if isinstance(c, dict) else getattr(c, "timestamp", 0))
            c_is_closed = bool(c.get("is_closed", False) if isinstance(c, dict) else getattr(c, "is_closed", False))
            c_close_ms = c_ts + dur
            if c_is_closed and c_close_ms <= now_ms:
                # Deep copy to isolate from concurrent WS stream mutation
                closed_immutable.append(
                    schemas.CandleCreate(
                        symbol=str(c.get("symbol", symbol) if isinstance(c, dict) else getattr(c, "symbol", symbol)),
                        timeframe=str(c.get("timeframe", tf) if isinstance(c, dict) else getattr(c, "timeframe", tf)),
                        timestamp=c_ts,
                        open=float(c.get("open", 0.0) if isinstance(c, dict) else getattr(c, "open", 0.0)),
                        high=float(c.get("high", 0.0) if isinstance(c, dict) else getattr(c, "high", 0.0)),
                        low=float(c.get("low", 0.0) if isinstance(c, dict) else getattr(c, "low", 0.0)),
                        close=float(c.get("close", 0.0) if isinstance(c, dict) else getattr(c, "close", 0.0)),
                        volume=float(c.get("volume", 0.0) if isinstance(c, dict) else getattr(c, "volume", 0.0)),
                        is_closed=True,
                    )
                )
                last_closed_ts = c_ts

        bundle[tf] = closed_immutable
        # Rule 061, 062: Real versioning per timeframe using last closed timestamp and count
        bundle["input_versions"][tf] = f"{tf}:{last_closed_ts}:{len(closed_immutable)}" if last_closed_ts else f"{tf}:EMPTY"
        bundle["last_closed_times"][tf] = last_closed_ts

    return bundle


def validated_quote_to_cg01_snapshot(
    q: Any,
    symbol: str = "XAUUSDT",
    raw_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    V16.21 Rule 042: Pure converter from ValidatedQuote to CG01 quote snapshot dictionary.
    Preserves exact bid, ask, last (distinct from mid), spread, exchange_time, observed_at,
    is_valid, is_stale, and rejection reason without mutating input.
    """
    bid = float(getattr(q, "bid", 0.0))
    ask = float(getattr(q, "ask", 0.0))
    q_last = float(getattr(q, "last", 0.0))
    mid = round((bid + ask) / 2.0, 4) if (bid > 0 and ask > 0) else bid
    last = q_last if q_last > 0 else (mid if mid > 0 else bid)
    spread = float(getattr(q, "spread", 0.0))
    if spread <= 0 and (bid > 0 and ask >= bid):
        spread = round(ask - bid, 4)

    is_valid = bool(getattr(q, "is_valid", False))
    is_stale = bool(getattr(q, "is_stale", False))
    rejection_code = getattr(q, "rejection_code", None) or ("QUOTE_STALE" if is_stale else ("QUOTE_UNAVAILABLE" if not is_valid else None))
    rejection_detail = getattr(q, "rejection_detail", None)

    exchange_time = getattr(q, "exchange_time", None)
    observed_at = int(getattr(q, "observed_at", 0))
    freshness_sec = float(getattr(q, "freshness_sec", 9999.0))

    meta = raw_meta or {}
    epoch = meta.get("connection_epoch") or meta.get("epoch")
    sequence = meta.get("sequence")
    source = meta.get("source", "COLLECTOR_TICKER")

    return {
        "symbol": symbol,
        "bid": bid,
        "ask": ask,
        "last": last,
        "mid": mid,
        "price": mid,
        "spread": spread,
        "exchange_time": exchange_time,
        "observed_at": observed_at,
        "timestamp": exchange_time or observed_at,
        "freshness_sec": freshness_sec,
        "is_valid": is_valid,
        "is_stale": is_stale,
        "rejection_code": rejection_code,
        "rejection_detail": rejection_detail,
        "source": source,
        "epoch": epoch,
        "sequence": sequence,
    }


def capture_cg01_quote_snapshot(
    symbol: str = "XAUUSDT",
    as_of_ms: Optional[int] = None,
    execution_now_ms: Optional[int] = None,
    clock: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    V16.21 / V16.24 Rules 014, 015, 031-046: Capture authoritative immutable quote snapshot from collector service.
    Normalizes quote with canonical execution quote validator without synthesizing artificial quotes.
    Freshness is strictly evaluated against execution_now_ms (current clock), not candle close time.
    Handles ValidatedQuote contract strictly: exchange_time, observed_at, last != mid.
    """
    from services.collector_service import collector_service
    from services.quote_validator import normalize_execution_quote

    if execution_now_ms is not None:
        now_ms = execution_now_ms
    elif as_of_ms is not None:
        now_ms = as_of_ms
    elif clock is not None:
        now_ms = clock.now_ms()
    else:
        now_ms = int(time.time() * 1000)
    raw_t = collector_service.latest_ticker
    if not raw_t:
        return {
            "symbol": symbol,
            "bid": 0.0,
            "ask": 0.0,
            "last": 0.0,
            "spread": 0.0,
            "mid": 0.0,
            "price": 0.0,
            "exchange_time": None,
            "observed_at": now_ms,
            "timestamp": now_ms,
            "freshness_sec": 9999.0,
            "is_valid": False,
            "is_stale": True,
            "rejection_code": "QUOTE_UNAVAILABLE",
            "rejection_detail": "Không có dữ liệu ticker từ collector feed",
            "source": "COLLECTOR_TICKER",
            "epoch": None,
            "sequence": None,
        }

    # Rule 040: Verify symbol matching
    raw_sym = raw_t.get("symbol") if isinstance(raw_t, dict) else getattr(raw_t, "symbol", None)
    if raw_sym and str(raw_sym).upper() != str(symbol).upper():
        return {
            "symbol": symbol,
            "bid": 0.0,
            "ask": 0.0,
            "last": 0.0,
            "spread": 0.0,
            "mid": 0.0,
            "price": 0.0,
            "exchange_time": None,
            "observed_at": now_ms,
            "timestamp": now_ms,
            "freshness_sec": 9999.0,
            "is_valid": False,
            "is_stale": True,
            "rejection_code": "SYMBOL_MISMATCH",
            "rejection_detail": f"Ticker symbol {raw_sym} does not match requested symbol {symbol}",
            "source": "COLLECTOR_TICKER",
            "epoch": None,
            "sequence": None,
        }

    q = normalize_execution_quote(raw_t, now_ms=now_ms)
    raw_meta = raw_t if isinstance(raw_t, dict) else {}
    return validated_quote_to_cg01_snapshot(q, symbol=symbol, raw_meta=raw_meta)


def resolve_cg01_financial_context(
    db: Optional[Session] = None,
) -> Dict[str, Any]:
    """
    V16.20 Rules 018, 025, 057: Resolve authoritative financial context (capital and risk_pct).
    Reuses the canonical risk settings and account audit source used by ACTIVE.
    Does NOT hardcode capital=1000.0 / risk_pct=0.25.
    """
    from services.risk_settings_service import risk_settings_service
    import crud
    from database import SessionLocal

    owns_db = False
    target_db = db
    if target_db is None:
        target_db = SessionLocal()
        owns_db = True

    try:
        g_settings = risk_settings_service.get_settings(target_db)
        audit = crud.get_or_create_today_audit(target_db)

        raw_eq = audit.current_equity if audit is not None else None
        capital_val = float(raw_eq) if raw_eq is not None else 1000.0

        risk_val = float(g_settings.risk_pct) if (g_settings and g_settings.risk_pct is not None) else 0.25

        return {
            "capital_usdt": capital_val,
            "risk_pct": risk_val,
            "leverage": int(g_settings.requested_leverage) if g_settings else 10,
            "margin_mode": str(g_settings.margin_mode) if g_settings else "isolated",
            "capital_source": "ACCOUNT_AUDIT",
            "risk_source": "RISK_SETTINGS",
        }
    finally:
        if owns_db:
            target_db.close()


def ensure_observation_run_started(
    symbol: str = "XAUUSDT",
    scope: str = "CG01",
    mode: str = "SHADOW",
    run_id: Optional[str] = None,
    initial_capital_usdt: float = 1000.0,
) -> Optional[Any]:
    """
    V16.20 Rules 027, 061-068: Managed start transaction for observation run.
    Uses its own dedicated DB session, validates mode/scope, commits, and returns an immutable run reference.
    Guarantees run persistence across session close and restart.
    Does NOT mutate global auto, risk settings, or trading policies.
    """
    from database import SessionLocal
    from services.paper_research_run_service import (
        PaperResearchRunService,
        PaperResearchRun,
    )
    import models

    db = SessionLocal()
    try:
        if run_id:
            row = db.query(models.PaperResearchRunRecord).filter(
                models.PaperResearchRunRecord.run_id == run_id
            ).first()
            if row:
                run = PaperResearchRun(
                    run_id=row.run_id,
                    symbol=row.symbol,
                    status=row.status,
                    started_at_ms=row.started_at_ms,
                    ended_at_ms=row.ended_at_ms,
                    software_version=row.software_version,
                    config_fingerprint=row.config_fingerprint,
                    execution_mode=row.execution_mode,
                    initial_capital_usdt=row.initial_capital_usdt,
                    observation_mode=row.observation_mode or mode,
                )
                PaperResearchRunService._active_runs[row.run_id] = run
                return run

        if not run_id:
            # Look up collecting run matching scope, symbol, and mode
            query = db.query(models.PaperResearchRunRecord).filter(
                models.PaperResearchRunRecord.scope == scope,
                models.PaperResearchRunRecord.symbol == symbol,
                models.PaperResearchRunRecord.status == "COLLECTING",
            )
            if hasattr(models.PaperResearchRunRecord, "observation_mode"):
                query = query.filter(models.PaperResearchRunRecord.observation_mode == mode)
            existing = query.order_by(models.PaperResearchRunRecord.started_at_ms.desc()).first()
            if existing:
                run = PaperResearchRun(
                    run_id=existing.run_id,
                    symbol=existing.symbol,
                    status=existing.status,
                    started_at_ms=existing.started_at_ms,
                    ended_at_ms=existing.ended_at_ms,
                    software_version=existing.software_version,
                    config_fingerprint=existing.config_fingerprint,
                    execution_mode=existing.execution_mode,
                    initial_capital_usdt=existing.initial_capital_usdt,
                    observation_mode=getattr(existing, "observation_mode", mode),
                )
                PaperResearchRunService._active_runs[existing.run_id] = run
                return run

        # Create and COMMIT new run in worker session
        t_now = int(time.time() * 1000)
        actual_run_id = run_id or f"run_{scope.lower()}_{symbol.lower()}_{mode.lower()}_{t_now}"
        exec_mode = "PAPER_EXECUTION" if mode == "ACTIVE" else "PAPER_SHADOW"

        rec = models.PaperResearchRunRecord(
            run_id=actual_run_id,
            scope=scope,
            symbol=symbol,
            status="COLLECTING",
            started_at_ms=t_now,
            ended_at_ms=None,
            software_version="V16.20",
            config_fingerprint=f"CG01_{mode}_V16_20",
            execution_mode=exec_mode,
            observation_mode=mode,
            initial_capital_usdt=initial_capital_usdt,
            current_capital_usdt=initial_capital_usdt,
            created_at_ms=t_now,
            updated_at_ms=t_now,
        )
        db.add(rec)
        db.commit()

        run = PaperResearchRun(
            run_id=actual_run_id,
            symbol=symbol,
            status="COLLECTING",
            started_at_ms=t_now,
            ended_at_ms=None,
            software_version="V16.20",
            config_fingerprint=f"CG01_{mode}_V16_20",
            execution_mode=exec_mode,
            initial_capital_usdt=initial_capital_usdt,
            observation_mode=mode,
        )
        PaperResearchRunService._active_runs[actual_run_id] = run
        return run
    except Exception as e:
        logger.error(f"Failed to start observation run: {e}")
        try:
            db.rollback()
        except Exception:
            pass
        raise
    finally:
        db.close()


def evaluate_cg01_shadow_snapshot(
    symbol: str,
    as_of_ms: int,
    bundle: Dict[str, Any],
    config: Optional[Any] = None,
    research_ctx: Optional[Any] = None,
    poi_context: Optional[Any] = None,
    current_quote: Optional[Dict[str, Any]] = None,
    capital: float = 1000.0,
    risk_pct: float = 0.25,
    costs: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Pure shadow evaluation adapter (Rules 041-045).
    Evaluates candidate without mutating ACTIVE singleton orchestrator state,
    watchlist (_active_zones), rejections (_arm_rejections), or emitting trade notifications.
    Reuses the canonical CG-01 evaluator (services.cg01_engine.evaluate_cg01_candidate).
    Uses canonical raw_by_tf bundle and effective configuration (Rules 021-023, 031-040).
    Never accesses .get() directly on candles (Rule 021).
    Never fabricates synthetic quotes when real quote is unavailable (Rule 024, 032, 043).
    """
    from services.cg01_engine import evaluate_cg01_candidate
    from services.scalp_watcher_orchestrator import serialize_cg01_evaluation
    from services.cg01_types import CG01Config
    import math

    cfg = config
    if cfg is None:
        try:
            eff_cfg, _, _ = resolve_effective_cg01_config(symbol=symbol)
            cfg = eff_cfg
        except Exception:
            cfg = CG01Config(profile_mode="PAPER_SHADOW")

    raw_by_tf = {
        "1M": bundle.get("1M") or [],
        "5M": bundle.get("5M") or [],
        "15M": bundle.get("15M") or [],
        "1H": bundle.get("1H") or [],
        "4H": bundle.get("4H") or [],
        "1D": bundle.get("1D") or bundle.get("D") or [],
    }

    c_1m = raw_by_tf["1M"]
    if not c_1m:
        return {
            "status": "SKIPPED",
            "candidate": None,
            "skip_reason": "WARMUP_INSUFFICIENT",
            "cg01_evaluation": serialize_cg01_evaluation(
                None, enabled=getattr(cfg, "enabled", True), evaluated=False, skip_reason="WARMUP_INSUFFICIENT", as_of_ms=as_of_ms
            ),
            "armed_order_id": None,
        }

    # Quote validation (Rules 023, 024, 043): Authoritative quote only, NO synthetic fallback
    q = current_quote
    if q is None:
        return {
            "status": "SKIPPED",
            "candidate": None,
            "skip_reason": "QUOTE_UNAVAILABLE",
            "cg01_evaluation": serialize_cg01_evaluation(
                None, enabled=getattr(cfg, "enabled", True), evaluated=False, skip_reason="QUOTE_UNAVAILABLE", as_of_ms=as_of_ms
            ),
            "armed_order_id": None,
        }

    is_valid = bool(getattr(q, "is_valid", False) if hasattr(q, "is_valid") else (q.get("is_valid", True) if isinstance(q, dict) and "is_valid" in q else True))
    is_stale = bool(getattr(q, "is_stale", False) if hasattr(q, "is_stale") else (q.get("is_stale", False) if isinstance(q, dict) else False))
    bid = float(getattr(q, "bid", 0.0) if hasattr(q, "bid") else (q.get("bid", 0.0) if isinstance(q, dict) else 0.0))
    ask = float(getattr(q, "ask", 0.0) if hasattr(q, "ask") else (q.get("ask", 0.0) if isinstance(q, dict) else 0.0))

    if not is_valid or is_stale or bid <= 0 or ask <= 0 or not math.isfinite(bid) or not math.isfinite(ask):
        return {
            "status": "SKIPPED",
            "candidate": None,
            "skip_reason": "QUOTE_UNAVAILABLE",
            "cg01_evaluation": serialize_cg01_evaluation(
                None, enabled=getattr(cfg, "enabled", True), evaluated=False, skip_reason="QUOTE_UNAVAILABLE", as_of_ms=as_of_ms
            ),
            "armed_order_id": None,
        }

    effective_costs = costs if costs is not None else getattr(cfg, "costs", None)

    cg01_dec = evaluate_cg01_candidate(
        raw_by_tf,
        q,
        config=cfg,
        now_ms=as_of_ms,
        capital=capital,
        risk_pct=risk_pct,
        costs=effective_costs,
    )

    eval_data = serialize_cg01_evaluation(cg01_dec, enabled=getattr(cfg, "enabled", True), evaluated=True, as_of_ms=as_of_ms)
    skip_reason = cg01_dec.reason_codes[0] if getattr(cg01_dec, "reason_codes", None) else getattr(cg01_dec, "skip_reason", None)
    status = "CANDIDATE_FOUND" if cg01_dec.candidate else ("SKIPPED" if skip_reason else "COMPLETED")

    return {
        "status": status,
        "candidate": cg01_dec.candidate,
        "skip_reason": skip_reason,
        "cg01_evaluation": eval_data,
        "armed_order_id": None,  # Shadow NEVER arms orders (Rule 040)
    }


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
        self._clock: Optional[Any] = None   # V16.24 Rule 040-042: Injected runtime/test clock

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
        self._active_eval_tasks: Set[Any] = set()
        self._active_eval_threads: Set[threading.Thread] = set()

    def set_clock(self, clock: Optional[Any]) -> None:
        """V16.24 Rule 040-042: Inject controlled runtime clock for tests / replay."""
        with self._lock:
            self._clock = clock

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

            changed = (new_enabled != self.enabled or new_shadow != self.shadow_mode or new_owner != self.is_owner)
            self.enabled = new_enabled
            self.shadow_mode = new_shadow
            self.is_owner = new_owner
            if new_enabled:
                self._stopped = False
            if changed:
                try:
                    from services.cg01_daily_bias_service import bump_runtime_ownership_epoch
                    bump_runtime_ownership_epoch()
                except Exception:
                    pass

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
                "active_eval_tasks_count": len([t for t in self._active_eval_tasks if not t.done()]),
                "active_eval_threads_count": len(self._active_eval_threads),
            }

    def is_running(self) -> bool:
        """Return True if background worker loop is active."""
        with self._lock:
            return self._running

    def reset_state(self) -> None:
        """Reset internal event deduplication, queues, and counters for test isolation."""
        with self._lock:
            try:
                from services.cg01_daily_bias_service import reset_runtime_ownership_epoch
                reset_runtime_ownership_epoch(1)
            except Exception:
                pass
            self._stopped = False
            self._running = False
            self._dirty_symbols.clear()
            self._symbol_in_flight.clear()
            self._seen_events.clear()
            self._last_evaluated_event_time.clear()
            self._pending_market_events.clear()
            self.events_received_count = 0
            self.evaluations_scheduled_count = 0
            self.duplicate_events_ignored_count = 0
            self.dropped_evaluations_count = 0
            self._active_eval_threads.clear()
            self._clock = None

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

            if getattr(self, "_stopped", False):
                return {"enqueued": False, "reason": "SCHEDULER_STOPPED", "event_id": event_id}

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
        market_evidence_as_of_ms: Optional[int] = None,
        execution_now_ms: Optional[int] = None,
        source_event: Optional[Dict[str, Any]] = None,
        clock: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Synchronously execute an event-driven evaluation cycle for a symbol with tracing.
        Respects single-flight per symbol (Rule 057).
        V16.24 Rules 031-060: Strict separation between:
        - market_evidence_as_of_ms (causal closed candles evidence boundary)
        - execution_now_ms (current clock for processing start, quote freshness, news, session, expiry, arming)
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
        from services.clock import live_clock

        effective_clock = clock or getattr(self, "_clock", None) or live_clock

        with self._lock:
            if symbol in self._symbol_in_flight:
                # Another evaluation is currently in flight for this symbol
                self._dirty_symbols.add(symbol)  # Ensure re-evaluates once current finishes
                return {"status": "DEFERRED_IN_FLIGHT", "symbol": symbol}
            self._symbol_in_flight.add(symbol)
            self._dirty_symbols.discard(symbol)

        ev = source_event or {}

        # 1. Market evidence time (closed candle timestamp boundary)
        evidence_as_of = market_evidence_as_of_ms
        if evidence_as_of is None:
            if ev.get("candle_timestamp"):
                dur = TF_DURATIONS_MS.get(ev.get("timeframe", "1M"), 60000)
                evidence_as_of = int(ev["candle_timestamp"]) + dur
            elif as_of_ms is not None:
                evidence_as_of = as_of_ms
            else:
                evidence_as_of = effective_clock.now_ms()

        # 2. Execution time (clock time for quote freshness, news blackout, session, expiry, arming)
        if execution_now_ms is not None:
            exec_now = execution_now_ms
        elif as_of_ms is not None:
            # Explicit replay / legacy test clock passed as as_of_ms (Rule 039)
            exec_now = as_of_ms
        else:
            exec_now = effective_clock.now_ms()

        t_now = exec_now
        trace_id = f"trc_{symbol}_{t_now}_{int(time.perf_counter() * 1000) % 100000}"

        trace = CG01EvaluationTrace(
            trace_id=trace_id,
            symbol=symbol,
            source_event_id=ev.get("event_id"),
            market_evidence_as_of_ms=evidence_as_of,
            exchange_event_time_ms=ev.get("exchange_event_time_ms"),
            event_received_at=ev.get("received_at_ms"),
            evidence_known_at=evidence_as_of,
            evidence_available_at=ev.get("received_at_ms"),
            evaluation_enqueued_at=ev.get("enqueued_at_ms"),
            evaluation_started_at=exec_now,
        )

        p_start = time.perf_counter()
        try:
            # Capture immutable input bundle up to causal market evidence time (Rule 062, Appendix A #3)
            bundle = capture_cg01_input_bundle(symbol=symbol, as_of_ms=evidence_as_of)
            trace.input_version = bundle["input_versions"].get("1M", 1)

            # Resolve authoritative quote snapshot evaluated at current execution time (Rules 041-046)
            quote_snap = capture_cg01_quote_snapshot(symbol=symbol, execution_now_ms=exec_now, clock=effective_clock)

            # Resolve authoritative financial context (Rules 018, 025, 057)
            fin_ctx = resolve_cg01_financial_context(db=db)
            effective_capital = fin_ctx["capital_usdt"]
            effective_risk = fin_ctx["risk_pct"]

            # Mode resolution
            obs_mode = "SHADOW" if self.shadow_mode else "ACTIVE"
            trace.observation_mode = obs_mode

            # Managed observation run start (Rules 027, 061-068)
            from services.paper_research_run_service import (
                associate_order_with_research_run,
                record_committed_order_milestone,
            )
            from services.cg01_types import CG01ResearchContext

            try:
                act_run = ensure_observation_run_started(
                    symbol=symbol,
                    scope="CG01",
                    mode=obs_mode,
                    initial_capital_usdt=effective_capital,
                )
                if act_run:
                    trace.run_id = act_run.run_id
            except Exception as run_err:
                logger.warning(f"Failed to ensure observation run started: {run_err}")
                trace.run_id = None

            eff_cfg, fingerprint, _ = resolve_effective_cg01_config(db=db, symbol=symbol)

            research_ctx = CG01ResearchContext(
                trace_id=trace_id,
                run_id=trace.run_id,
                segment_id=None,
                symbol=symbol,
                config_fingerprint=fingerprint,
                input_versions=bundle.get("input_versions", {}),
                evaluation_as_of_ms=exec_now,
                observation_mode=obs_mode,
            )

            # Shadow isolation (Rules 041-045):
            # In shadow mode, invoke pure shadow adapter without mutating active singleton orchestrator.
            if self.shadow_mode:
                span_engine = CG01Span(name="engine_evaluation_shadow", start_perf=time.perf_counter())
                cycle_result = evaluate_cg01_shadow_snapshot(
                    symbol=symbol,
                    as_of_ms=evidence_as_of,
                    bundle=bundle,
                    config=eff_cfg,
                    research_ctx=research_ctx,
                    current_quote=quote_snap,
                    capital=effective_capital,
                    risk_pct=effective_risk,
                    costs=getattr(eff_cfg, "costs", None),
                )
                span_engine.finish()
            else:
                # Active production evaluation (Rules 048, 049)
                effective_auto = None if self.is_owner else False
                span_engine = CG01Span(name="engine_evaluation", start_perf=time.perf_counter())
                cycle_result = scalp_watcher_orchestrator.evaluate_cycle(
                    db=db,
                    symbol=symbol,
                    now_ms=exec_now,
                    clock=effective_clock,
                    candles_1m=bundle.get("1M"),
                    candles_5m=bundle.get("5M"),
                    candles_15m=bundle.get("15M"),
                    candles_1h=bundle.get("1H"),
                    candles_4h=bundle.get("4H"),
                    candles_1d=bundle.get("1D") or bundle.get("D"),
                    quote=quote_snap,
                    auto_enabled=effective_auto,
                    research_context=research_ctx,
                    caller_identity="SCHEDULER",
                    evaluation_origin="SCHEDULER",
                )
                span_engine.finish()
            trace.total_runtime_ms = span_engine.duration_ms

            t_done = int(effective_clock.now_ms() if effective_clock else time.time() * 1000)
            trace.evaluation_completed_at = t_done

            cg_eval = cycle_result.get("cg01_evaluation") or {}
            trace.candidate_key = cg_eval.get("candidate_key")
            trace.armed_order_id = cycle_result.get("armed_order_id")
            cycle_status = cycle_result.get("status", "COMPLETED")
            trace.evaluation_status = cycle_status
            trace.skip_reason = cg_eval.get("skip_reason")

            if trace.armed_order_id and db is not None:
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

            # Record telemetry trace in ring buffer and durable store (Rule 051: never pass caller db)
            cg01_trace_recorder.record_trace(trace, db=None)
            self.evaluations_scheduled_count += 1

            return {
                "status": "COMPLETED",
                "processing_status": "COMPLETED",
                "evaluation_status": cycle_status,
                "cycle_status": cycle_status,
                "symbol": symbol,
                "trace_id": trace_id,
                "run_id": trace.run_id,
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

                    async def _run_eval(s=sym, ev=source_ev):
                        def _do_eval():
                            cur_th = threading.current_thread()
                            with self._lock:
                                self._active_eval_threads.add(cur_th)
                            db = SessionLocal()
                            try:
                                ev_evidence_as_of = None
                                if ev:
                                    dur = TF_DURATIONS_MS.get(ev.get("timeframe", "1M"), 60000)
                                    if ev.get("candle_timestamp"):
                                        ev_evidence_as_of = int(ev["candle_timestamp"]) + dur
                                    elif ev.get("received_at_ms"):
                                        ev_evidence_as_of = int(ev["received_at_ms"])
                                return self.evaluate_symbol_now(
                                    db,
                                    s,
                                    market_evidence_as_of_ms=ev_evidence_as_of,
                                    source_event=ev,
                                    clock=getattr(self, "_clock", None),
                                )
                            finally:
                                try:
                                    db.close()
                                finally:
                                    with self._lock:
                                        self._active_eval_threads.discard(cur_th)
                        return await asyncio.to_thread(_do_eval)

                    eval_task = asyncio.create_task(_run_eval(sym, source_ev))
                    with self._lock:
                        self._active_eval_tasks.add(eval_task)
                    eval_task.add_done_callback(lambda t: self._active_eval_tasks.discard(t))
                    await eval_task

            except asyncio.CancelledError:
                self._running = False
                break
            except Exception as e:
                logger.error(f"CG01EvaluationScheduler loop error: {e}", exc_info=True)
                await asyncio.sleep(1.0)

    async def wait_evaluations_drained(self, timeout_s: float = 3.0) -> bool:
        """
        V16.22 / V16.23 Rules 027, 043, 072, 073, 133: Wait for all in-flight evaluation tasks
        and dispatched worker threads to finish.
        Ensures thread-dispatched evaluations finish before trace persistence drains,
        even if the async coroutine wrapper was cancelled.
        """
        start_t = time.monotonic()
        while (time.monotonic() - start_t) < timeout_s:
            with self._lock:
                pending = [t for t in self._active_eval_tasks if not t.done()]
                pending_threads = len(self._active_eval_threads)
            if not pending and pending_threads == 0:
                return True
            await asyncio.sleep(0.05)
        return False

    def stop(self) -> None:
        """V16.22 Rule 042: Stop accepting new events and signal loop to terminate."""
        with self._lock:
            self._running = False
            self._stopped = True
            try:
                from services.cg01_daily_bias_service import bump_runtime_ownership_epoch
                bump_runtime_ownership_epoch()
            except Exception:
                pass
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
    Appendix B & Rules 023, 046-065: Resolve effective CG01 config via canonical factory.
    Guarantees exact parity between ACTIVE orchestrator and SHADOW scheduler.
    """
    from services.cg01_config_factory import resolve_canonical_cg01_config
    return resolve_canonical_cg01_config(
        db=db,
        symbol=symbol,
        base_config=base_config,
    )
