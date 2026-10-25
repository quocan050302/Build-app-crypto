"""
backend/services/observation_interval_service.py
Authoritative Observation Interval Producer for AURUM V17.6.4.2 (ECI-01, ECI-12, ECI-13).

Consolidates continuous engine heartbeats and evaluation cycles into durable
PaperObservationInterval records in the database.
Enforces:
1. Continuous evidence pairing within bounded gaps (<= 45s).
2. Fail-closed quality differentiation (GOOD, DEGRADED, DOWN, UNVERIFIED).
3. Conservative quality transition boundary semantics: prior proven quality ends at last_ts.
4. Database isolation: persists to caller engine's bind or SessionLocal with retries and rollbacks.
5. In-memory tracker advances ONLY on confirmed DB commit.
6. Sealed pending state machine (ACTIVE -> SEALED_PENDING -> PERSISTING -> COMMITTED -> ACKNOWLEDGED)
   ensuring closed intervals are NEVER discarded if a database write fails.
7. Periodic checkpointing and graceful flush with full logical idempotency.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import logging
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy.orm import Session, sessionmaker

import models
from database import SessionLocal

logger = logging.getLogger("aurum.observation_interval_service")


@dataclass(frozen=True)
class IntervalPersistenceResult:
    status: str  # "COMMITTED", "ALREADY_EXISTS", "RETRYABLE_FAILURE", "PERMANENT_FAILURE"
    interval_id: Optional[int] = None
    error_code: Optional[str] = None
    retryable: bool = False


@dataclass(frozen=True)
class IntervalFlushResult:
    attempted: int
    committed: int
    already_exists: int
    retryable_failed: int
    permanent_failed: int
    pending_count: int

    def __int__(self) -> int:
        return self.committed

    def __index__(self) -> int:
        return self.committed

    def __eq__(self, other):
        if isinstance(other, int):
            return self.committed == other
        return super().__eq__(other)

    def __ge__(self, other):
        if isinstance(other, int):
            return self.committed >= other
        return super().__ge__(other)

    def __gt__(self, other):
        if isinstance(other, int):
            return self.committed > other
        return super().__gt__(other)

    def __le__(self, other):
        if isinstance(other, int):
            return self.committed <= other
        return super().__le__(other)

    def __lt__(self, other):
        if isinstance(other, int):
            return self.committed < other
        return super().__lt__(other)


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

        # ECI-01: Pending intervals map for intervals sealed but unconfirmed in DB.
        # Key: stable logical identity tuple
        # Value: dict with interval data, lifecycle status, attempts, error
        self._pending_intervals: Dict[Tuple, Dict[str, Any]] = {}

        self._last_persist_error: Optional[str] = None
        self._last_successful_interval_commit_ms: Optional[int] = None

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
                return SessionLocal(), True
            except Exception as e:
                logger.warning(f"Could not open fallback SessionLocal for interval producer: {e}")
                return None, False

    def _make_pending_key(self, data: Dict[str, Any]) -> Tuple:
        """ECI-01 & ECI-12: Full stable logical identity of an interval segment across retries."""
        return (
            data.get("run_id"),
            str(data.get("symbol", "XAUUSDT")).upper(),
            str(data.get("scope", "CG01")).upper(),
            str(data.get("worker_role", "WATCHER_PROCESS")).upper(),
            str(data.get("engine_instance_id") or ""),
            str(data.get("evidence_origin", "LIVE_FORWARD")).upper(),
            str(data.get("mode", "PAPER")).upper(),
            int(data.get("start_ms", 0)),
            int(data.get("end_ms", 0)),
            str(data.get("data_quality", "UNVERIFIED")).upper(),
        )

    def seal_active_interval(
        self,
        active: Dict[str, Any],
        *,
        end_ms: int,
    ) -> Optional[Dict[str, Any]]:
        """
        ECI-01: Safely seals a closed/checkpointed segment into the durable pending state.
        Must be called while self._lock is held.
        """
        start_ms = active.get("start_ms", 0)
        if end_ms <= start_ms or start_ms <= 0:
            return None

        snapshot = dict(active)
        snapshot["end_ms"] = end_ms
        pkey = self._make_pending_key(snapshot)
        if pkey not in self._pending_intervals:
            self._pending_intervals[pkey] = {
                "data": snapshot,
                "status": "SEALED_PENDING",
                "attempts": 0,
                "sealed_at_ms": int(time.time() * 1000),
                "last_error": None,
                "last_attempt_at_ms": None,
            }
        return snapshot

    def persist_pending_intervals(
        self,
        db: Optional[Session],
        *,
        max_attempts: int = 3,
    ) -> IntervalFlushResult:
        """
        ECI-01: Attempts to persist all sealed pending intervals to the database.
        Evicts an interval from pending memory ONLY upon confirmed COMMITTED or ALREADY_EXISTS.
        Retains pending segments across retries on SQLite lock or transient failure.
        """
        with self._lock:
            # Create a snapshot copy of pending items to process outside the lock
            items_to_persist = list(self._pending_intervals.items())

        attempted = 0
        committed = 0
        already_exists = 0
        retryable_failed = 0
        permanent_failed = 0

        for pkey, pending_info in items_to_persist:
            attempted += 1
            interval_data = pending_info["data"]

            # Lifecycle transition: PERSISTING
            with self._lock:
                if pkey in self._pending_intervals:
                    self._pending_intervals[pkey]["status"] = "PERSISTING"
                    self._pending_intervals[pkey]["attempts"] += 1
                    self._pending_intervals[pkey]["last_attempt_at_ms"] = int(time.time() * 1000)

            res = self._persist_interval_record(db, interval_data)

            with self._lock:
                if res.status in ("COMMITTED", "ALREADY_EXISTS"):
                    # Lifecycle transition: COMMITTED -> ACKNOWLEDGED (evicted)
                    self._pending_intervals.pop(pkey, None)
                    if res.status == "COMMITTED":
                        committed += 1
                    else:
                        already_exists += 1
                    self._last_successful_interval_commit_ms = int(time.time() * 1000)
                    self._last_persist_error = None
                elif res.status == "RETRYABLE_FAILURE":
                    # Lifecycle transition: RETRYABLE_FAILURE -> SEALED_PENDING
                    if pkey in self._pending_intervals:
                        self._pending_intervals[pkey]["status"] = "SEALED_PENDING"
                        self._pending_intervals[pkey]["last_error"] = res.error_code
                    retryable_failed += 1
                    self._last_persist_error = res.error_code
                else:
                    # Lifecycle transition: PERMANENT_FAILURE -> FAILED_PENDING
                    if pkey in self._pending_intervals:
                        self._pending_intervals[pkey]["status"] = "FAILED_PENDING"
                        self._pending_intervals[pkey]["last_error"] = res.error_code
                    permanent_failed += 1
                    self._last_persist_error = res.error_code

        with self._lock:
            remaining_pending = len(self._pending_intervals)

        return IntervalFlushResult(
            attempted=attempted,
            committed=committed,
            already_exists=already_exists,
            retryable_failed=retryable_failed,
            permanent_failed=permanent_failed,
            pending_count=remaining_pending,
        )

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
        Enforces origin isolation, sealed pending state, and fail-closed quality transitions.
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
                # Case 1: Out of order or duplicate timestamp (<= 0 gap) -> ignore
                if gap <= 0:
                    return None

                # Case 2: Gap exceeded max bounded gap (> 45s)
                if gap > self._max_gap_ms:
                    if active["end_ms"] > active["start_ms"]:
                        self.seal_active_interval(active, end_ms=active["end_ms"])
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
                    prev_end = active["last_ts"]
                    if prev_end > active["start_ms"]:
                        self.seal_active_interval(active, end_ms=prev_end)
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

                # Case 4: Max span reached or periodic checkpoint cadence reached (EVID-06 & ECI-01)
                elif (t_now - active["start_ms"]) >= self._max_interval_span_ms or (t_now - active["start_ms"]) >= self._checkpoint_interval_ms:
                    active["end_ms"] = t_now
                    active["heartbeats_count"] += 1
                    active["last_ts"] = t_now
                    self.seal_active_interval(active, end_ms=t_now)
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

        # Persist all sealed pending intervals outside the lock (ECI-01 Part 027)
        flush_res = self.persist_pending_intervals(db)
        if flush_res.committed > 0 or flush_res.already_exists > 0:
            return {"status": "COMMITTED", "flushed_count": flush_res.committed + flush_res.already_exists}
        return None

    def _persist_interval_record(self, db: Optional[Session], data: Dict[str, Any]) -> IntervalPersistenceResult:
        """
        Persists a closed interval record to the database with retry and rollback (EVID-03, ECI-01, ECI-12).
        Enforces complete logical identity to prevent false duplicate collisions.
        """
        target_db, close_session = self._resolve_session(db)
        if target_db is None:
            return IntervalPersistenceResult(status="PERMANENT_FAILURE", error_code="NO_DB_SESSION")

        try:
            # Check for duplicate interval with full logical identity (ECI-12, Part 037)
            existing = target_db.query(models.PaperObservationInterval).filter(
                models.PaperObservationInterval.run_id == data.get("run_id"),
                models.PaperObservationInterval.symbol == data.get("symbol", "XAUUSDT"),
                models.PaperObservationInterval.scope == data.get("scope", "CG01"),
                models.PaperObservationInterval.worker_role == data.get("worker_role", "WATCHER_PROCESS"),
                models.PaperObservationInterval.start_ms == data["start_ms"],
                models.PaperObservationInterval.end_ms == data["end_ms"],
                models.PaperObservationInterval.data_quality == data.get("data_quality", "UNVERIFIED"),
            ).first()
            if existing is not None:
                return IntervalPersistenceResult(status="ALREADY_EXISTS", interval_id=existing.id)

            for attempt in range(3):
                try:
                    record = models.PaperObservationInterval(
                        run_id=data.get("run_id"),
                        symbol=data.get("symbol", "XAUUSDT"),
                        scope=data.get("scope", "CG01"),
                        worker_role=data.get("worker_role", "WATCHER_PROCESS"),
                        mode=data.get("mode", "PAPER"),
                        start_ms=data["start_ms"],
                        end_ms=data["end_ms"],
                        data_quality=data.get("data_quality", "UNVERIFIED"),
                        heartbeats_count=data.get("heartbeats_count", 1),
                        created_at_ms=data.get("end_ms", int(time.time() * 1000)),
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
                    err_msg = str(e).lower()
                    is_locked = "locked" in err_msg or "busy" in err_msg
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

    def flush_active_intervals(self, db: Optional[Session]) -> IntervalFlushResult:
        """
        ECI-01 & ECI-13: Flushes all currently active unpersisted intervals and pending intervals.
        In-memory active tracker advances start_ms ONLY on confirmed DB commit.
        Returns typed IntervalFlushResult (compatible with int comparison).
        """
        active_to_persist = []
        with self._lock:
            for key, active in list(self._active_intervals.items()):
                if active["end_ms"] > active["start_ms"]:
                    snapshot = self.seal_active_interval(active, end_ms=active["end_ms"])
                    if snapshot:
                        active_to_persist.append((
                            key,
                            snapshot["start_ms"],
                            snapshot["end_ms"],
                            snapshot["heartbeats_count"],
                            self._make_pending_key(snapshot),
                        ))

        # Persist all pending intervals
        flush_res = self.persist_pending_intervals(db)

        # Advance active start_ms ONLY for intervals whose commit was confirmed
        with self._lock:
            for key, s_ms, e_ms, hb_cnt, pkey in active_to_persist:
                if pkey not in self._pending_intervals:
                    cur = self._active_intervals.get(key)
                    if cur and cur["start_ms"] == s_ms:
                        cur["start_ms"] = e_ms
                        cur["heartbeats_count"] = max(0, cur["heartbeats_count"] - hb_cnt)

        return flush_res

    def get_pending_metrics(self, run_id: Optional[str] = None) -> Dict[str, Any]:
        """ECI-01 Part 041: Exposes operational pending queue metrics."""
        with self._lock:
            now_ms = int(time.time() * 1000)
            oldest_age_ms = 0
            matching = [
                item for item in self._pending_intervals.values()
                if run_id is None or item.get("data", {}).get("run_id") == run_id
            ]
            if matching:
                oldest_sealed = min(item.get("sealed_at_ms", now_ms) for item in matching)
                oldest_age_ms = max(0, now_ms - oldest_sealed)

            return {
                "pending_intervals_count": len(matching),
                "pending_count": len(matching),
                "oldest_pending_age_ms": oldest_age_ms,
                "last_persist_error": self._last_persist_error,
                "last_successful_interval_commit_ms": self._last_successful_interval_commit_ms,
            }

    def clear(self) -> None:
        with self._lock:
            self._active_intervals.clear()
            self._pending_intervals.clear()
            self._last_persist_error = None
            self._last_successful_interval_commit_ms = None


observation_interval_service = ObservationIntervalService()
