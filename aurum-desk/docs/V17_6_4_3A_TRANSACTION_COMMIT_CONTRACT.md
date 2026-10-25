# AURUM V17.6.4.3a Transaction Commit Truth Contract

## Executive Summary
In distributed telemetry and database-backed trading systems, **a SQL `flush()` is not a database `commit()`**.
`flush()` sends pending SQL statements into the current database transaction buffer on the active connection.
It does not establish durable persistence; a subsequent transaction rollback, process crash, or connection drop completely purges flushed rows.

Under **Master Prompt V17.6.4.3a (AC43A-01, AC43A-02, AC43A-09)**, the observation telemetry system enforces strict transactional commit truth.

---

## Transaction Result States

| State | Definition | Authorized Origin | Lifecycle & Queue Behavior |
|---|---|---|---|
| `COMMITTED` | Transaction committed to disk; row verified durable. | Telemetry-owned transaction commit (`close_session=True`), or verified commit acknowledgement. | Pending interval is acknowledged and safely evicted from memory. |
| `PENDING_COMMIT` | SQL statements executed and flushed inside caller-owned transaction (`close_session=False`); outer transaction has not yet committed. | `ObservationIntervalService._persist_interval_record()` when `close_session=False`. | Pending interval is **retained** in memory; pending count is NOT decremented. |
| `ROLLED_BACK` | Caller-owned transaction rolled back. Rows never reach disk. | Outer caller rollback event. | Pending interval remains or is invalidated. No durable commit is ever reported. |
| `ALREADY_EXISTS` | An identical interval identity was verified as already committed in the database. | Independent database connection probe. | Evicted as already durably stored. Never returned for caller's own uncommitted insert. |
| `RETRYABLE_FAILURE`| Transient lock contention (e.g. SQLite `busy`) or temporary I/O issue. | Telemetry persistence handler. | Retained in queue for exponential backoff retry. |
| `PERMANENT_FAILURE`| Schema constraint violation or fatal database error. | Telemetry persistence handler. | Logged with stack trace; alert triggered. |

---

## Core Invariants

### 1. No Auto-Commit on Borrowed Sessions
The observation service **must never** call `target_db.commit()` on a borrowed session provided by a caller (e.g., `ScalpWatcherOrchestrator` or order execution pipeline). Calling `commit()` on a caller session would prematurely commit unconfirmed business operations (such as `PaperOrder` entries or state transitions).

### 2. No Premature Pending Interval Eviction
Calling `persist_pending_intervals(db)` on a borrowed session flushes intervals to the session and marks them `PENDING_COMMIT`. The intervals remain inside the `_pending_intervals` queue and are only acknowledged once the outer business transaction successfully commits.

### 3. Independent Readback Verification
To ensure a row is truly committed and not just visible within the caller session's uncommitted transaction buffer, queries verifying durability or checking `ALREADY_EXISTS` must use an independent connection / session.

### 4. Rollback Isolation
If the caller's business transaction rolls back:
- Flushed interval records and paper orders are discarded by the database engine.
- An independent session querying `models.PaperObservationInterval` finds 0 rows.
- Engine health and dashboard metrics reflect uncommitted / unverified status rather than false durability.
