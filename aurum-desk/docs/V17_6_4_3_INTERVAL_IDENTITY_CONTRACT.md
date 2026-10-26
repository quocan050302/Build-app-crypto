# V17.6.4.3 Interval Identity Contract & Database-Enforced Idempotency

## Executive Overview
Under Aurum V17.6.4.3 (`DBEA-05`, `DBEA-06`), observation intervals possess an immutable, mathematically deterministic SHA-256 identity (`interval_identity`) that aligns in-memory tracking with the relational database schema.

Before V17.6.4.3, interval deduplication relied on application-level checks or partial composite keys. Under high concurrency, worker crashes, or network retries, duplicate intervals could be written to the database, or retry attempts could fail with unhandled integrity errors that stalled the persistence worker.

V17.6.4.3 resolves this by embedding `interval_identity` directly into the database schema as a unique constraint, coupled with idempotent reconciliation in the persistence pipeline.

---

## 1. Identity Formula & Cryptographic Hashing

Every observation interval is assigned an immutable SHA-256 hash generated from all identifying environmental and temporal dimensions:

```python
hash_input = (
    f"{run_id}:"
    f"{symbol}:"
    f"{scope}:"
    f"{worker_role}:"
    f"{evidence_origin}:"
    f"{engine_instance_id}:"
    f"{mode}:"
    f"{interval_start_ms}:"
    f"{interval_end_ms}:"
    f"{db_identity}"
)
interval_identity = hashlib.sha256(hash_input.encode("utf-8")).hexdigest()
```

### Component Breakdown
| Dimension | Example Value | Description |
|---|---|---|
| `run_id` | `RUN-2026-10-25-01` | Unique lifecycle execution identifier |
| `symbol` | `XAUUSDT` | Target traded instrument |
| `scope` | `M1` | Aggregation window (1-minute, 5-minute, etc.) |
| `worker_role` | `WATCHER_PROCESS` | Dedicated watcher vs scheduler worker |
| `evidence_origin` | `LIVE_FEED` | Real exchange feed vs replay / test origin |
| `engine_instance_id` | `core-worker-a` | Specific engine/process instance identifier |
| `mode` | `PAPER` | Operating mode (`PAPER`, `LIVE`, `REPLAY`) |
| `interval_start_ms` | `1792771200000` | Epoch start timestamp (ms) |
| `interval_end_ms` | `1792771260000` | Epoch end timestamp (ms) |
| `db_identity` | `sqlite:////data.db` | Target database connection identity |

---

## 2. Relational Schema & Constraints

In `backend/models.py`, `PaperObservationInterval` defines the following schema:

```python
class PaperObservationInterval(Base):
    __tablename__ = "paper_observation_intervals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(64), nullable=False, index=True)
    symbol = Column(String(20), default="XAUUSDT", nullable=False, index=True)
    scope = Column(String(20), default="M1", nullable=False)
    worker_role = Column(String(32), default="UNKNOWN", nullable=False)
    evidence_origin = Column(String(32), default="LIVE_FEED", nullable=False)
    engine_instance_id = Column(String(64), default="aurum-core-default", nullable=False)
    mode = Column(String(20), default="PAPER", nullable=False)
    interval_start_ms = Column(BigInteger, nullable=False, index=True)
    interval_end_ms = Column(BigInteger, nullable=False, index=True)
    interval_identity = Column(String(64), nullable=False, unique=True, index=True)
    # ... telemetry & quality metrics ...
```

### Unique Index
The table enforces a database-level unique index:
```sql
CREATE UNIQUE INDEX uq_paper_obs_interval_identity ON paper_observation_intervals(interval_identity);
```

---

## 3. Idempotent Upsert & Reconciliation Logic

When `ObservationIntervalService.flush_pending_intervals()` writes intervals to disk:

1. **Transaction Isolation**:
   - Each interval insert or batch is wrapped in a dedicated savepoint or transaction block.

2. **Duplicate Conflict Handling**:
   - If an `IntegrityError` or unique constraint violation occurs:
     - The service intercepts the exception and suppresses failure.
     - It queries the existing row by `interval_identity`.
     - If the newly completed interval contains higher-quality evidence (e.g., more `good_ticks` or fresher telemetry), the existing record is updated in place.
     - The in-memory pending item is marked successfully resolved and removed from `_pending_queue`.

3. **Guaranteed Zero Memory Leaks**:
   - Duplicate flushes or retries never leave orphaned items in RAM, guaranteeing that `pending_count` returns to zero once writes complete.
