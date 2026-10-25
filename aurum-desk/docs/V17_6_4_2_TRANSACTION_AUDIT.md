# AURUM DESK — V17.6.4.2 TRANSACTION & PERSISTENCE AUDIT
## Session Ownership, Persistence Truth & Independent Telemetry Commits

**Document Version:** 1.0.0  
**Target Release:** V17.6.4.2  
**Baseline SHA:** `f033d4aa69aef03901ab672173b24c8df236b35e`  
**Classification:** Database Architecture · Transaction Integrity · Persistence Truth

---

### 1. Executive Summary & Defect Remediation

In prior releases (V17.6.4.1 and earlier, **ECI-03**, **ECI-06**, **ECI-07**):
1. **Flushed-Only Reported as COMMITTED (**ECI-07**)**:
   When `record_evaluation_cycle()` used a caller-provided session that only performed `db.flush()`, it reported `persistence_status = "COMMITTED"` before the caller ever issued a database transaction commit.
2. **RAM Diagnostic Fallback Masking DB Absence (**ECI-06**)**:
   When `get_latest_persisted_cycle()` found no row in the database, it fell back to in-memory `get_latest_cycle_diagnostic()`, presenting uncommitted RAM state under a contract explicitly named "persisted".
3. **Optimistic Persistence Defaulting (**ECI-03**)**:
   The observation dashboard snapshot defaulted `evidence_persistence_status` to `"COMMITTED"` without actively verifying database rows, concealing uncommitted intervals or database degradation.

V17.6.4.2 establishes strict transaction isolation between business trading state (paper orders) and observability telemetry, coupled with authoritative persistence status reporting.

---

### 2. Session Ownership Architecture

Telemetry recording methods must persist observability evidence durably without prematurely committing the caller's business transaction or interfering with rollback semantics.

[`_resolve_target_session`](file:///Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/backend/services/engine_observability_service.py) governs session resolution:

```python
def _resolve_target_session(self, db: Optional[Session] = None, borrow_session: bool = False):
    if db is not None:
        if borrow_session:
            # Borrow caller's session: flush only, do not commit or close
            return db, False
        bind = db.get_bind()
        if bind is not None:
            # Bind an independent owned session to commit independently
            return sessionmaker(bind=bind)(), True
    # Independent owned session from factory
    return self._get_db_session(), True
```

#### Behavioral Contract

- **Default Path (`borrow_session=False`)**:
  Creates an independent `OwnedSession` on `db.get_bind()`. Telemetry is committed to disk immediately and the session is closed cleanly (`close_session=True`). Caller's business entities are unaffected.
  Resulting status: `persistence_status = "COMMITTED"`.
- **Borrowing Path (`borrow_session=True`)**:
  Used only when transactional bundling is explicitly requested. Performs `db.flush()`.
  Resulting status: `persistence_status = "PENDING_COMMIT"`.

---

### 3. Removal of In-Memory Diagnostics Fallback (**ECI-06**)

In [`EngineObservabilityService.get_latest_persisted_cycle`](file:///Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/backend/services/engine_observability_service.py), the fallback to uncommitted memory was eliminated:

```python
# V17.6.4.1 (DEPRECATED & REMOVED):
# if not row:
#     return self.get_latest_cycle_diagnostic(symbol=symbol, run_id=run_id)

# V17.6.4.2 (STRICT DATABASE TRUTH):
if not row:
    return None
```

If a cycle has not been committed to disk, `get_latest_persisted_cycle` returns strictly `None`.

---

### 4. Authoritative Persistence Status Resolution

In `build_engine_health_snapshot()` and `build_observation_dashboard_snapshot()`, `evidence_persistence_status` is resolved via the following hierarchy:

1. **`NO_ACTIVE_RUN`**:
   No active run ID exists for the engine instance.
2. **`UNVERIFIED`**:
   Database session is unavailable, or the target run ID lacks a verified cycle record or interval record in the database.
3. **`DEGRADED`**:
   `ObservationIntervalService.get_pending_metrics()` reports uncommitted intervals in the pending queue (`pending_count > 0` or `failed_count > 0`).
4. **`COMMITTED`**:
   Both the latest evaluation cycle and the latest observation interval are confirmed persisted in disk tables, and zero pending items remain in the retry queue.

---

### 5. Verification Test Matrix

- **`CT01 - CT12`** (`test_v17_6_4_2_cycle_commit_truth.py`):
  Verifies independent commit, session borrowing flush-only status (`PENDING_COMMIT`), idempotency deduplication, and zero in-memory fallback. (12/12 PASS)
- **`PS01 - PS12`** (`test_v17_6_4_2_persistence_status.py`):
  Verifies status resolution (`COMMITTED`, `DEGRADED`, `UNVERIFIED`, `NO_ACTIVE_RUN`) across health snapshots and dashboard endpoints. (12/12 PASS)
- **`WI01 - WI07`** (`test_v17_6_4_2_watcher_integration.py`):
  Verifies end-to-end watcher execution with independent database session readback, feed degradation, rotation retry recovery, and run isolation. (7/7 PASS)
