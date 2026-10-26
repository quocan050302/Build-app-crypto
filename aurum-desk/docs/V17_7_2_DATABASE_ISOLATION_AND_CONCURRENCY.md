# AURUM DESK V17.7.2 — DATABASE ISOLATION AND CONCURRENCY VERIFICATION
**Release:** V17.7.2  
**Focus:** Test Database Isolation Guard, Temporary DB Containment, Multi-Threaded Arming Race  
**Status:** VERIFIED_FROM_TEST  

---

## 1. Test Database Isolation Architecture (P0-04 & CE-17)

### 1.1 The P0-04 Hazard in V17.7.1
In V17.7.1, tests imported `engine` and `SessionLocal` from `backend.database`. If `AURUM_DB_PATH` was set or defaulted to the local application database (`aurum_desk.db`), the test suite fixture `clean_v17_7_db` executed destructive queries:
```python
db.query(SystemConfig).filter(SystemConfig.key.like("stop_placement_v1.%")).delete()
```
This posed a critical safety hazard: running tests could purge or corrupt production configuration and live trading records.

### 1.2 The V17.7.2 Isolation Architecture
In `backend/database.py`, test isolation is enforced before engine binding:
```python
is_pytest = (
    "pytest" in sys.modules
    or "PYTEST_CURRENT_TEST" in os.environ
    or os.environ.get("TESTING") in ("1", "true", "True")
)

if is_pytest:
    env_db = os.environ.get("AURUM_TEST_DB_PATH") or os.environ.get("AURUM_DB_PATH")
    if not env_db or "test" not in env_db.lower():
        _test_dir = tempfile.mkdtemp(prefix="aurum_test_")
        DATABASE_PATH = os.path.join(_test_dir, "aurum_test.db")
    else:
        DATABASE_PATH = env_db
```

### 1.3 Strict Fail-Closed Isolation Assertion (`assert_test_database_isolated`)
A mandatory isolation guard function was introduced:
```python
def assert_test_database_isolated(db_path: Optional[str] = None) -> None:
    path = db_path or DATABASE_PATH
    norm_path = os.path.abspath(path).lower()
    if "aurum_desk.db" in norm_path and "test" not in norm_path:
        raise RuntimeError(
            f"CRITICAL SAFETY HAZARD: Test execution targeted runtime database: {path}."
        )
```
- Every test in `test_v17_7_adaptive_stop_loss.py` invokes `assert_test_database_isolated()` at setup and teardown.
- `CE-17` tests that passing a non-test path (e.g., `aurum_desk.db`) immediately raises `RuntimeError`, blocking execution.

---

## 2. Multi-Threaded Concurrency & Idempotency Races (P1-03, SL-57, DSL-18, CE-16)

### 2.1 The P1-03 Defect in V17.7.1
V17.7.1 claimed concurrent idempotency testing, but executed sequential inserts:
```python
db.add(order1)
db.commit()
db.add(order2) # failed with IntegrityError
```
Sequential insertion proves primary key uniqueness, but fails to test true multi-threaded transaction interleaving, connection pool races, or lock contention.

### 2.2 Synchronized Multi-Threaded Barrier Race (CE-16 & SL-57)
In V17.7.2, two independent threads running on distinct database sessions race to persist the same order simultaneously:
```python
barrier = threading.Barrier(2)

def _arm_worker(worker_id: int):
    db = SessionLocal()
    try:
        order = PaperOrder(
            id=shared_order_id,
            symbol="XAUUSDT",
            direction="LONG",
            planned_entry=2700.0,
            stop_loss=2690.0,
            take_profit=2720.0,
            quantity=1.0,
            initial_risk_usdt=10.0,
            created_at=1000000 + worker_id,
            state="armed"
        )
        db.add(order)
        barrier.wait(timeout=5.0)  # Synchronize threads exactly at commit point
        db.commit()
        results.append((worker_id, "SUCCESS"))
    except Exception as e:
        db.rollback()
        results.append((worker_id, f"ROLLBACK: {type(e).__name__}"))
    finally:
        db.close()

t1 = threading.Thread(target=_arm_worker, args=(1,))
t2 = threading.Thread(target=_arm_worker, args=(2,))
t1.start(); t2.start()
t1.join(); t2.join()
```

### 2.3 Verified Invariants
1. Exactly 1 thread succeeds; the second rolls back safely.
2. The database contains exactly 1 durable order row for the given ID.
3. No database corruption, unhandled deadlocks, or phantom duplicate orders occur.
