# AURUM DESK V17.7.3 — CONCURRENT ARM INTEGRITY
**Production ARM Boundary Idempotency, Mutex Lock & Durable Commit Serialization**  
**Repository:** `quocan050302/Build-app-crypto`  
**Branch:** `feat/v17-7-adaptive-structural-stop-loss`  
**Status:** `VERIFIED_FROM_CONCURRENT_RUNTIME_TEST`

---

## 1. Concurrency Architecture & Threat Model

In automated high-frequency scalp evaluation, multiple asynchronous worker loops or background trigger threads may simultaneously discover the same valid market opportunity (e.g., simultaneous M1 sweep reclaim and continuation signals on XAUUSDT).

### The Concurrency Hazard (Defect V173-P1-03)
If two threads attempt to ARM the same opportunity simultaneously:
1. **Double Order Allocation**: Two distinct `PaperOrder` records could be created for the same setup, doubling position risk.
2. **Quota / CAS Bypass**: Both threads check `armed_open_count < max_positions` before either has committed, exceeding the portfolio position cap.
3. **Previous Test Weakness**: Prior tests only asserted SQLite primary key uniqueness by directly executing `db.add(PaperOrder(id=same_id))` across threads. This verified the database driver, but failed to exercise the production orchestrator entry pipeline.

---

## 2. Production ARM Remediation Architecture

### 2.1 The Critical Section in `ScalpWatcherOrchestrator._arm_paper_candidate()`
In `backend/services/scalp_watcher_orchestrator.py`:
```python
with serialize_entry_mutation(active_session):
    # 1. Check if an active order already exists for this candidate/setup
    existing_order = (
        db.query(PaperOrder)
        .filter(
            PaperOrder.setup_id == candidate.get("setup_id"),
            PaperOrder.state.in_(["candidate", "armed", "paper_open"]),
        )
        .first()
    )
    if existing_order:
        logger.info(f"Idempotent ARM: Found existing order {existing_order.id}")
        return existing_order

    # 2. Re-check daily risk limits and active positions under lock
    active_count = db.query(PaperOrder).filter(PaperOrder.state.in_(["armed", "paper_open"])).count()
    if active_count >= max_concurrent_positions:
        logger.warning("Active position cap reached under lock; rejecting ARM.")
        return None

    # 3. Create and persist new PaperOrder
    order = PaperOrder(
        id=f"ord_{uuid4().hex[:12]}",
        setup_id=candidate.get("setup_id"),
        instrument=candidate.get("symbol", "XAUUSDT"),
        direction=candidate.get("direction"),
        state="armed",
        planned_entry=candidate.get("entry_price"),
        stop_loss=candidate.get("stop_loss"),
        take_profit=candidate.get("take_profit"),
        quantity=candidate.get("quantity"),
        armed_at=int(time.time() * 1000),
    )
    db.add(order)
    db.commit()  # <--- V17.7.3 CRITICAL REPAIR: Commit inside lock!
    db.refresh(order)
    return order
```

### 2.2 Why Commit Inside the Lock is Essential
Without `db.commit()` inside the lock:
1. Thread A acquires `serialize_entry_mutation`.
2. Thread A adds `PaperOrder` to session and releases lock without committing.
3. Thread B acquires `serialize_entry_mutation` on an independent SQLite connection.
4. Because Thread A's transaction is not committed, SQLite's WAL/isolation hides Thread A's pending row from Thread B's query.
5. Thread B also adds a `PaperOrder`, resulting in either duplicate orders or a dirty rollback crash.
By committing before releasing `serialize_entry_mutation`, Thread B is guaranteed to see Thread A's committed order and safely return the existing order idempotently.

---

## 3. Concurrency Test Execution Proof (`test_ce_16`)

In `backend/tests/test_v17_7_adaptive_stop_loss.py`:
- `test_ce_16_concurrent_real_arm_barrier_single_order` constructs two distinct `SessionLocal()` instances connecting to the same test-owned database.
- A `threading.Barrier(2)` synchronizes both threads to execute `orch._arm_paper_candidate()` at the exact same instant.
- **Assertions:**
  1. Both threads complete without unhandled exceptions or deadlocks.
  2. Exactly 1 `PaperOrder` is durably written to the database:
     `assert db.query(PaperOrder).count() == 1`
  3. Both thread results return the exact same order ID:
     `assert results[0].id == results[1].id`
  4. The order state is durably recorded as `armed`.

### Execution Evidence:
```bash
./backend/venv/bin/python -m pytest backend/tests/test_v17_7_adaptive_stop_loss.py -k "test_ce_16" -v
============================= test session starts ==============================
collected 100 items / 99 deselected / 1 selected

backend/tests/test_v17_7_adaptive_stop_loss.py::test_ce_16_concurrent_real_arm_barrier_single_order PASSED [100%]

======================= 1 passed, 99 deselected in 0.35s =======================
```
