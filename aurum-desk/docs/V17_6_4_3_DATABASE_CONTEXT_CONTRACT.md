# V17.6.4.3 Database Context Contract & Multi-Database Telemetry Isolation

## Executive Overview
Under Aurum V17.6.4.3 (`DBEA-01`, `DBEA-02`, `DBEA-08`), the observation interval subsystem implements complete, airtight telemetry isolation across distinct database instances, sessions, and operational modes.

Prior to V17.6.4.3, observation interval memory queues were keyed only by `(run_id, symbol, scope, worker_role)`. In multi-database topologies—such as replay simulations running in an isolated SQLite database concurrently with live paper trading in a persistent database, or parallel unit test fixtures utilizing separate in-memory engines—telemetry intervals could bleed across database boundaries, leading to cross-database write attempts, corrupted retry counters, and false degraded persistence states.

This contract defines the authoritative specification for database context resolution, queue partitioning, transaction boundaries, and isolation guarantees across all observation services.

---

## 1. Database Identity Resolution (`resolve_observation_database_identity`)

Every database connection, session, engine, or connection URL is resolved into an immutable, canonical string identity (`DatabaseIdentity`) via `resolve_observation_database_identity()` in `backend/services/observation_interval_service.py`.

### Resolution Rules

1. **Transient In-Memory SQLite Instances (`:memory:`, `sqlite:///:memory:`, `sqlite://`)**:
   - Because each SQLite in-memory database connection or engine represents a distinct private address space, identical strings like `":memory:"` must **not** be treated as the same database identity across separate engine objects.
   - For an engine or connection object referencing `:memory:`, the identity is formed as:
     ```
     sqlite:///:memory:<id(engine)>
     ```
     or
     ```
     sqlite:///:memory:<id(session.bind)>
     ```
   - This ensures that two distinct in-memory engines initialized within the same process remain 100% isolated.

2. **File-Backed SQLite Databases**:
   - File paths (whether provided as relative paths, absolute paths, or `sqlite:///` URIs) are resolved to their canonical, normalized absolute filesystem path:
     ```
     sqlite:////absolute/path/to/aurum_paper.db
     ```
   - Symlinks and relative paths (`./data/dev.db` vs `data/dev.db`) resolve to the exact same canonical string, preventing accidental partition splintering for the same physical file.

3. **Remote RDBMS (PostgreSQL, MySQL)**:
   - Formatted as `dialect://host:port/database_name`.
   - Credentials (passwords) are scrubbed from telemetry keys to prevent secret leakage in log traces.

4. **Borrowed Sessions vs Direct Engines**:
   - If a `Session` or `scoped_session` is provided, the identity is resolved from `session.bind` (or engine attached to session).
   - If `session.bind` is unavailable or `session` is None, resolution fails closed to `"UNKNOWN_DATABASE"`.

---

## 2. In-Memory Queue Key Tuple

In `ObservationIntervalService`, all active and pending intervals in RAM are partitioned strictly by an 8-tuple key:

```python
IntervalQueueKey = Tuple[
    str,  # run_id (e.g., "R-2026-10-25-001")
    str,  # symbol (e.g., "XAUUSDT")
    str,  # scope (e.g., "M1")
    str,  # worker_role (e.g., "WATCHER_PROCESS", "SCHEDULER")
    str,  # evidence_origin ("LIVE_FEED", "REPLAY_SIM", "SYNTHETIC_TEST")
    str,  # engine_instance_id (e.g., "engine-worker-1")
    str,  # mode ("PAPER", "REPLAY", "LIVE")
    str,  # db_identity (e.g., "sqlite:////path/to/db.sqlite")
]
```

### Invariants:
- **No Shared Queues**: Intervals destined for `Database A` can never reside in the same pending queue as intervals for `Database B`.
- **Thread Safety**: All reads and writes to active intervals (`_active_intervals`) and pending flush buffers (`_pending_queue`) are protected by an internal reentrant lock (`threading.RLock()`).

---

## 3. Flush & Persistence Boundaries (`flush_pending_intervals`)

When `flush_pending_intervals(db_session, expected_db_identity=None)` is called:

1. **Target Identity Validation**:
   - The session's `db_identity` is resolved via `resolve_observation_database_identity(db_session)`.
   - If `expected_db_identity` is explicitly passed and does not match the session's resolved identity, the flush aborts immediately without attempting any SQL operations.

2. **Queue Filtering**:
   - The flush operation extracts and processes **only** intervals whose `db_identity` matches the session.
   - Pending intervals belonging to other database identities remain safely untouched in their respective queue partitions.

3. **Database Write Isolation**:
   - SQL `INSERT` statements are issued exclusively on the provided `db_session`.
   - If a write failure occurs (e.g., disk I/O error or SQLite locked error) on `Database A`, only items destined for `Database A` record retry increments and error backoff. The pending metrics and persistence health of `Database B` remain unaffected (`GOOD` / `HEALTHY`).

---

## 4. Pending Metrics & Commit State Isolation

`get_pending_metrics(db_identity=None, symbol=None, run_id=None)`:
- Returns pending counts, oldest pending age, retry attempts, and `last_persist_error` scoped specifically to the requested `db_identity`.
- Guarantees that a transient failure in a test database or secondary replay database will not mark the live operational dashboard as `DEGRADED`.
