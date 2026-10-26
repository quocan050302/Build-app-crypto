# AURUM DESK V17.6.4.1 — TRANSACTION AUDIT
## Database Session Isolation, Retry Semantics & Durable Telemetry Persistence

### 1. Architectural Boundary Isolation
To guarantee that telemetry logging never corrupts or aborts critical business transactions (order execution, risk guards, position tracking), V17.6.4.1 establishes strict transaction isolation:

```
[Business Operation Session]             [Telemetry Operation Session]
(PaperOrder / Decision)                   (Heartbeat / ObservationInterval)
         |                                               |
  Active Transaction                              Dedicated Session
         |                                        (autocommit / rollback)
  db.commit() / db.rollback()                            |
         |                                    _resolve_session(db) -> target_db
  NO TELEMETRY SIDE-EFFECTS                    COMMITTED / ROLLBACK ISOLATED
```

- When `db` is supplied to `ObservationIntervalService`, `_resolve_session(db)` binds to the underlying engine and creates an isolated, short-lived session.
- Any failure in the business session does NOT corrupt the telemetry session.
- Any failure in the telemetry session does NOT abort order placement or trigger premature strategy stops.

---

### 2. Interval Persistence Result Enum & Retry Protocol

In previous versions, a database exception during interval flush was logged but treated as flushed, resulting in uncommitted intervals being discarded from memory.

V17.6.4.1 introduces `IntervalPersistenceResult`:
- `COMMITTED`: Interval record successfully written and committed to SQLite disk.
- `ALREADY_EXISTS`: Interval with identical natural key already persisted (idempotent no-op).
- `RETRYABLE_FAILURE`: Transient database lock (`OperationalError: database is locked`) or I/O contention. Eligible for bounded retry.
- `PERMANENT_FAILURE`: Schema corruption, bad type, or unrecoverable error.

#### Bounded Retry Protocol:
```python
max_retries = 3
backoff_base = 0.05s

for attempt in range(max_retries):
    try:
        session.add(record)
        session.commit()
        return IntervalPersistenceResult.COMMITTED
    except OperationalError as exc:
        session.rollback()  # MANDATORY rollback before retry!
        time.sleep(backoff_base * (2 ** attempt))
```
- A failed attempt **MUST** execute `session.rollback()` before attempting retry to clear dirty transaction state.
- In-memory active interval state is **NOT** advanced or purged until `IntervalPersistenceResult.COMMITTED` is confirmed.
- Uncommitted intervals remain in `_active_intervals` for subsequent flushes, ensuring zero evidence loss.

---

### 3. Periodic Checkpointing (EVID-06)
To prevent telemetry evidence loss during ungraceful process crashes:
- `ObservationIntervalService` runs periodic checkpoints every 30 seconds.
- Spanning intervals are flushed to disk as closed segments, and a new segment opened immediately.
- If the worker process is killed (`SIGKILL` or power outage), at most 30 seconds of telemetry can be affected.

---

### 4. Cycle Commit Consistency (EVID-09)
`EngineObservabilityService.record_evaluation_cycle`:
- `persistence_status` is explicitly tracked.
- Set to `COMMITTED` if and only if `db.commit()` succeeds.
- If `db.commit()` raises an exception, the exception is caught, logged, and `persistence_status` is set to `FAILED_DB_WRITE` (or in-memory cache).
- Telemetry logging inside cycle evaluation wraps heartbeat operations in isolated `try/except` blocks to prevent cascading failures.
