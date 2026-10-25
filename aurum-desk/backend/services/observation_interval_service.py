"""
backend/services/observation_interval_service.py
Authoritative Observation Interval Producer for AURUM V17.6.4 (Phase F, Parts 081-100).

Consolidates continuous engine heartbeats and evaluation cycles into durable
PaperObservationInterval records in the database.
Enforces:
1. Continuous evidence pairing within bounded gaps (<= 45s).
2. Quality differentiation (GOOD, DEGRADED, DOWN).
3. Database isolation: persists to caller engine's bind or SessionLocal.
4. Thread-safe, idempotent interval extension and rotation.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Optional, Tuple
from sqlalchemy.orm import Session, sessionmaker

import models

logger = logging.getLogger("aurum.observation_interval_service")


class ObservationIntervalService:
    def __init__(self, max_gap_ms: int = 45_000, max_interval_span_ms: int = 300_000):
        self._lock = threading.Lock()
        self._max_gap_ms = max_gap_ms
        self._max_interval_span_ms = max_interval_span_ms
        # Active tracking key -> dict
        # Key: (run_id, symbol, scope, worker_role)
        self._active_intervals: Dict[Tuple[Optional[str], str, str, str], Dict[str, Any]] = {}

    def _resolve_session(self, db: Optional[Session]) -> Tuple[Optional[Session], bool]:
        """Resolves an owned session bound to the caller's database engine (Phase E)."""
        bind = None
        if db is not None:
            try:
                bind = db.get_bind()
            except Exception:
                pass

        if bind is not None:
            try:
                TargetSession = sessionmaker(bind=bind, autoflush=False)
                return TargetSession(), True
            except Exception as e:
                logger.warning(f"Could not bind session for observation interval: {e}")
                return db, False
        elif db is not None:
            return db, False
        else:
            try:
                from database import SessionLocal
                return SessionLocal(), True
            except Exception as e:
                logger.warning(f"Could not open fallback SessionLocal for interval producer: {e}")
                return None, False

    def record_heartbeat_event(
        self,
        db: Optional[Session],
        *,
        run_id: Optional[str] = None,
        symbol: str = "XAUUSDT",
        scope: str = "CG01",
        worker_role: str = "WATCHER_PROCESS",
        mode: str = "PAPER",
        timestamp_ms: Optional[int] = None,
        is_feed_healthy: bool = True,
        is_quote_fresh: bool = True,
        engine_instance_id: Optional[str] = None,
        evidence_origin: str = "LIVE_FORWARD",
    ) -> Optional[Dict[str, Any]]:
        """
        Processes a single heartbeat event and updates/persists durable observation intervals (PART 086-096).
        """
        # Exclude replay evidence from live forward observation coverage (PART 094)
        if evidence_origin.upper() == "REPLAY":
            return None

        t_now = int(timestamp_ms if timestamp_ms is not None else int(time.time() * 1000))
        if t_now <= 0:
            return None

        # Determine quality
        if not is_feed_healthy or not is_quote_fresh:
            quality = "DEGRADED"
        else:
            quality = "GOOD"

        key = (run_id, symbol.upper(), scope.upper(), worker_role.upper())
        interval_to_persist = None

        with self._lock:
            active = self._active_intervals.get(key)
            if active is None:
                # Start new active tracking window
                self._active_intervals[key] = {
                    "run_id": run_id,
                    "symbol": symbol.upper(),
                    "scope": scope.upper(),
                    "worker_role": worker_role.upper(),
                    "mode": mode.upper(),
                    "start_ms": t_now,
                    "end_ms": t_now,
                    "data_quality": quality,
                    "heartbeats_count": 1,
                    "last_ts": t_now,
                }
                return None
            else:
                gap = t_now - active["last_ts"]
                # Case 1: Out of order timestamp (<= 0 gap) -> ignore or update count
                if gap <= 0:
                    return None

                # Case 2: Gap exceeded max bounded gap (> 45s)
                if gap > self._max_gap_ms:
                    # Finalize previous interval if it has meaningful span
                    if active["end_ms"] > active["start_ms"]:
                        interval_to_persist = dict(active)
                    # Reset active interval starting from t_now
                    self._active_intervals[key] = {
                        "run_id": run_id,
                        "symbol": symbol.upper(),
                        "scope": scope.upper(),
                        "worker_role": worker_role.upper(),
                        "mode": mode.upper(),
                        "start_ms": t_now,
                        "end_ms": t_now,
                        "data_quality": quality,
                        "heartbeats_count": 1,
                        "last_ts": t_now,
                    }

                # Case 3: Quality changed or max span reached (e.g. 5 minutes)
                elif active["data_quality"] != quality or (t_now - active["start_ms"]) >= self._max_interval_span_ms:
                    # Extend previous up to t_now and persist it
                    active["end_ms"] = t_now
                    active["heartbeats_count"] += 1
                    interval_to_persist = dict(active)
                    # Start new interval beginning at t_now
                    self._active_intervals[key] = {
                        "run_id": run_id,
                        "symbol": symbol.upper(),
                        "scope": scope.upper(),
                        "worker_role": worker_role.upper(),
                        "mode": mode.upper(),
                        "start_ms": t_now,
                        "end_ms": t_now,
                        "data_quality": quality,
                        "heartbeats_count": 1,
                        "last_ts": t_now,
                    }

                # Case 4: Normal continuous extension within same quality & bounded gap
                else:
                    active["end_ms"] = t_now
                    active["heartbeats_count"] += 1
                    active["last_ts"] = t_now

        # If an interval was closed, persist to database
        if interval_to_persist and interval_to_persist["end_ms"] > interval_to_persist["start_ms"]:
            self._persist_interval_record(db, interval_to_persist)
            return interval_to_persist

        return None

    def _persist_interval_record(self, db: Optional[Session], data: Dict[str, Any]) -> None:
        """Persists a closed interval record to the database."""
        target_db, close_session = self._resolve_session(db)
        if target_db is None:
            return

        try:
            # Check for duplicate interval (idempotency PART 090)
            existing = target_db.query(models.PaperObservationInterval).filter(
                models.PaperObservationInterval.run_id == data["run_id"],
                models.PaperObservationInterval.symbol == data["symbol"],
                models.PaperObservationInterval.scope == data["scope"],
                models.PaperObservationInterval.start_ms == data["start_ms"],
                models.PaperObservationInterval.end_ms == data["end_ms"],
            ).first()
            if existing is None:
                record = models.PaperObservationInterval(
                    run_id=data["run_id"],
                    symbol=data["symbol"],
                    scope=data["scope"],
                    worker_role=data["worker_role"],
                    mode=data["mode"],
                    start_ms=data["start_ms"],
                    end_ms=data["end_ms"],
                    data_quality=data["data_quality"],
                    heartbeats_count=data["heartbeats_count"],
                    created_at_ms=data["end_ms"],
                )
                target_db.add(record)
                if close_session:
                    target_db.commit()
                else:
                    target_db.flush()
        except Exception as e:
            if close_session:
                target_db.rollback()
            logger.warning(f"Error persisting PaperObservationInterval: {e}")
        finally:
            if close_session:
                target_db.close()

    def flush_active_intervals(self, db: Optional[Session]) -> int:
        """Flushes all currently active unpersisted intervals to database."""
        flushed_count = 0
        to_persist = []
        with self._lock:
            for key, active in list(self._active_intervals.items()):
                if active["end_ms"] > active["start_ms"]:
                    to_persist.append(dict(active))
                    # Reset start_ms to end_ms
                    active["start_ms"] = active["end_ms"]
                    active["heartbeats_count"] = 0

        for item in to_persist:
            self._persist_interval_record(db, item)
            flushed_count += 1

        return flushed_count

    def clear(self) -> None:
        with self._lock:
            self._active_intervals.clear()


observation_interval_service = ObservationIntervalService()
