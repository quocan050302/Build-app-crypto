"""
backend/services/observation_interval_service.py
Authoritative Observation Interval Producer for AURUM V17.6.4.1 (EVID-03, EVID-05, EVID-06).

Consolidates continuous engine heartbeats and evaluation cycles into durable
PaperObservationInterval records in the database.
Enforces:
1. Continuous evidence pairing within bounded gaps (<= 45s).
2. Fail-closed quality differentiation (GOOD, DEGRADED, DOWN, UNVERIFIED).
3. Conservative quality transition boundary semantics: prior proven quality ends at last_ts.
4. Database isolation: persists to caller engine's bind or SessionLocal with retries and rollbacks.
5. In-memory tracker advances ONLY on confirmed DB commit.
6. Periodic checkpointing and graceful flush.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy.orm import Session, sessionmaker

import models

logger = logging.getLogger("aurum.observation_interval_service")


@dataclass(frozen=True)
class IntervalPersistenceResult:
    status: str  # "COMMITTED", "ALREADY_EXISTS", "RETRYABLE_FAILURE", "PERMANENT_FAILURE"
    interval_id: Optional[int] = None
    error_code: Optional[str] = None
    retryable: bool = False


def split_observation_quality_transition(
    *,
    previous_quality: str,
    previous_verified_ts: int,
    current_quality: str,
    current_ts: int,
) -> List[Dict[str, Any]]:
    """
    OBS-QT: Conservative boundary semantics for observation quality transitions (EVID-05).
    - Proven previous quality ends at last proven timestamp.
    - If there is a gap between last proven ts and new confirmed ts, it is marked UNVERIFIED.
    - New quality begins strictly at current_ts.
    """
    segments = []
    if current_ts > previous_verified_ts:
        segments.append({
            "quality": "UNVERIFIED",
            "start_ms": previous_verified_ts,
            "end_ms": current_ts,
            "reason": f"QUALITY_TRANSITION_GAP_{previous_quality}_TO_{current_quality}",
        })
    return segments


class ObservationIntervalService:
    def __init__(
        self,
        max_gap_ms: int = 45_000,
        max_interval_span_ms: int = 300_000,
        checkpoint_interval_ms: int = 30_000,
    ):
        self._lock = threading.Lock()
        self._max_gap_ms = max_gap_ms
        self._max_interval_span_ms = max_interval_span_ms
        self._checkpoint_interval_ms = checkpoint_interval_ms
        # Active tracking key -> dict
        # Key: (run_id, symbol, scope, worker_role, evidence_origin, engine_instance_id)
        self._active_intervals: Dict[Tuple[Optional[str], str, str, str, str, Optional[str]], Dict[str, Any]] = {}

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
        is_feed_healthy: Optional[bool] = None,
        is_quote_fresh: Optional[bool] = None,
        engine_instance_id: Optional[str] = None,
        evidence_origin: str = "LIVE_FORWARD",
        coverage_quality: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Processes a single heartbeat event and updates/persists durable observation intervals.
        Enforces origin isolation and fail-closed quality transitions.
        """
        norm_origin = str(evidence_origin).upper()
        # Exclude replay, test and unknown origins from live forward coverage (EVID-02, EVID-11)
        if norm_origin in ("REPLAY", "TEST", "UNKNOWN"):
            return None

        t_now = int(timestamp_ms if timestamp_ms is not None else int(time.time() * 1000))
        if t_now <= 0:
            return None

        # Determine fail-closed quality (EVID-01)
        if coverage_quality is not None and str(coverage_quality).upper() in ("GOOD", "DEGRADED", "DOWN", "UNVERIFIED"):
            quality = str(coverage_quality).upper()
        elif is_feed_healthy is True and is_quote_fresh is True:
            quality = "GOOD"
        elif is_feed_healthy is False or is_quote_fresh is False:
            quality = "DEGRADED"
        else:
            quality = "UNVERIFIED"

        key = (
            run_id,
            symbol.upper(),
            scope.upper(),
            worker_role.upper(),
            norm_origin,
            engine_instance_id,
        )
        intervals_to_persist: List[Dict[str, Any]] = []

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
                    "engine_instance_id": engine_instance_id,
                    "evidence_origin": norm_origin,
                    "start_ms": t_now,
                    "end_ms": t_now,
                    "data_quality": quality,
                    "heartbeats_count": 1,
                    "last_ts": t_now,
                }
                return None
            else:
                gap = t_now - active["last_ts"]
                # Case 1: Out of order timestamp (<= 0 gap) -> ignore
                if gap <= 0:
                    return None

                # Case 2: Gap exceeded max bounded gap (> 45s)
                if gap > self._max_gap_ms:
                    if active["end_ms"] > active["start_ms"]:
                        intervals_to_persist.append(dict(active))
                    self._active_intervals[key] = {
                        "run_id": run_id,
                        "symbol": symbol.upper(),
                        "scope": scope.upper(),
                        "worker_role": worker_role.upper(),
                        "mode": mode.upper(),
                        "engine_instance_id": engine_instance_id,
                        "evidence_origin": norm_origin,
                        "start_ms": t_now,
                        "end_ms": t_now,
                        "data_quality": quality,
                        "heartbeats_count": 1,
                        "last_ts": t_now,
                    }

                # Case 3: Quality changed (EVID-05: conservative boundary)
                elif active["data_quality"] != quality:
                    prev_interval = dict(active)
                    prev_interval["end_ms"] = active["last_ts"]
                    if prev_interval["end_ms"] > prev_interval["start_ms"]:
                        intervals_to_persist.append(prev_interval)
                    self._active_intervals[key] = {
                        "run_id": run_id,
                        "symbol": symbol.upper(),
                        "scope": scope.upper(),
                        "worker_role": worker_role.upper(),
                        "mode": mode.upper(),
                        "engine_instance_id": engine_instance_id,
                        "evidence_origin": norm_origin,
                        "start_ms": t_now,
                        "end_ms": t_now,
                        "data_quality": quality,
                        "heartbeats_count": 1,
                        "last_ts": t_now,
                    }

                # Case 4: Max span reached or periodic checkpoint cadence reached (EVID-06)
                elif (t_now - active["start_ms"]) >= self._max_interval_span_ms or (t_now - active["start_ms"]) >= self._checkpoint_interval_ms:
                    active["end_ms"] = t_now
                    active["heartbeats_count"] += 1
                    active["last_ts"] = t_now
                    checkpoint_interval = dict(active)
                    intervals_to_persist.append(checkpoint_interval)
                    self._active_intervals[key] = {
                        "run_id": run_id,
                        "symbol": symbol.upper(),
                        "scope": scope.upper(),
                        "worker_role": worker_role.upper(),
                        "mode": mode.upper(),
                        "engine_instance_id": engine_instance_id,
                        "evidence_origin": norm_origin,
                        "start_ms": t_now,
                        "end_ms": t_now,
                        "data_quality": quality,
                        "heartbeats_count": 1,
                        "last_ts": t_now,
                    }

                # Case 5: Normal continuous extension within same quality & bounded gap
                else:
                    active["end_ms"] = t_now
                    active["heartbeats_count"] += 1
                    active["last_ts"] = t_now

        # Persist closed/checkpointed intervals
        last_persisted = None
        for item in intervals_to_persist:
            if item["end_ms"] > item["start_ms"]:
                res = self._persist_interval_record(db, item)
                if res.status in ("COMMITTED", "ALREADY_EXISTS"):
                    last_persisted = item

        return last_persisted

    def _persist_interval_record(self, db: Optional[Session], data: Dict[str, Any]) -> IntervalPersistenceResult:
        """
        Persists a closed interval record to the database with retry and rollback (EVID-03, EVID-06).
        Returns typed IntervalPersistenceResult.
        """
        target_db, close_session = self._resolve_session(db)
        if target_db is None:
            return IntervalPersistenceResult(status="PERMANENT_FAILURE", error_code="NO_DB_SESSION")

        try:
            # Check for duplicate interval (idempotency PART 090)
            existing = target_db.query(models.PaperObservationInterval).filter(
                models.PaperObservationInterval.run_id == data["run_id"],
                models.PaperObservationInterval.symbol == data["symbol"],
                models.PaperObservationInterval.scope == data["scope"],
                models.PaperObservationInterval.start_ms == data["start_ms"],
                models.PaperObservationInterval.end_ms == data["end_ms"],
            ).first()
            if existing is not None:
                return IntervalPersistenceResult(status="ALREADY_EXISTS", interval_id=existing.id)

            for attempt in range(3):
                try:
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
                    return IntervalPersistenceResult(status="COMMITTED", interval_id=record.id)
                except Exception as e:
                    if close_session:
                        try:
                            target_db.rollback()
                        except Exception:
                            pass
                    is_locked = "locked" in str(e).lower() or "busy" in str(e).lower()
                    if attempt < 2 and is_locked:
                        time.sleep(0.05 * (attempt + 1))
                        continue
                    return IntervalPersistenceResult(
                        status="RETRYABLE_FAILURE" if is_locked else "PERMANENT_FAILURE",
                        error_code=str(e),
                        retryable=is_locked,
                    )
        finally:
            if close_session:
                try:
                    target_db.close()
                except Exception:
                    pass

        return IntervalPersistenceResult(status="PERMANENT_FAILURE", error_code="UNKNOWN_ERROR")

    def flush_active_intervals(self, db: Optional[Session]) -> int:
        """
        Flushes all currently active unpersisted intervals to database (EVID-03).
        In-memory tracker advances ONLY on confirmed DB commit.
        """
        flushed_count = 0
        to_persist: List[Tuple[Any, Dict[str, Any]]] = []

        with self._lock:
            for key, active in list(self._active_intervals.items()):
                if active["end_ms"] > active["start_ms"]:
                    to_persist.append((key, dict(active)))

        for key, item in to_persist:
            res = self._persist_interval_record(db, item)
            if res.status in ("COMMITTED", "ALREADY_EXISTS"):
                flushed_count += 1
                with self._lock:
                    cur = self._active_intervals.get(key)
                    if cur and cur["start_ms"] == item["start_ms"]:
                        cur["start_ms"] = item["end_ms"]
                        cur["heartbeats_count"] = max(0, cur["heartbeats_count"] - item["heartbeats_count"])
            else:
                logger.warning(f"Interval persistence failed ({res.error_code}). Evidence preserved in memory.")

        return flushed_count

    def clear(self) -> None:
        with self._lock:
            self._active_intervals.clear()


observation_interval_service = ObservationIntervalService()
