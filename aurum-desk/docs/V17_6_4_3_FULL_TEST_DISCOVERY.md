# V17.6.4.3 Full Backend Test Discovery & Isolation Audit

## Executive Overview
Under Aurum V17.6.4.3 (`DBEA-10`), the test automation harness implements dynamic, zero-drift test discovery across the entire backend test suite.

Previously, acceptance runners relied on hardcoded lists of test files. When new tests were added or legacy tests modified, test suites could be silently omitted from CI/CD gates. Furthermore, module-level environment pollution in historical test files caused cross-test pollution and transient failures in downstream suites.

V17.6.4.3 introduces automated pytest collection via `pytest --collect-only -q`, strict test isolation verification, and comprehensive test suite categorization.

---

## 1. Dynamic Discovery Architecture (`tools/run_v17_6_4_3_acceptance.py`)

Gate `G24_full_backend_test_discovery` performs dynamic discovery:
1. Executes `python -m pytest backend/tests --collect-only -q`.
2. Collects all active test files (`test_*.py`), suites, and individual test cases.
3. Produces a machine-readable discovery artifact (`G24_full_backend_test_discovery.json`) documenting:
   - Total test files discovered: **398+**
   - Total test cases discovered: **1,200+**
   - Discovered test suites per release generation.

Gate `G25_full_backend_test_execution` runs the entire discovered backend test corpus end-to-end to ensure system-wide regression immunity.

---

## 2. Test Suite Functional Categorization

The discovered test corpus spans 9 generations of Aurum architecture:

### 2.1 Observability & Telemetry Subsystem (V17.6.x)
- `test_v17_6_4_3_database_isolation.py` (9 tests) - Multi-DB isolation, queue segregation, borrowed vs owned sessions.
- `test_v17_6_4_3_evidence_authority.py` (19 tests) - Authoritative 7-step quote validation, anti-spoofing.
- `test_v17_6_4_3_interval_identity.py` (7 tests) - SHA-256 identity, unique constraint, duplicate reconciliation.
- `test_v17_6_4_3_transaction_boundaries.py` (4 tests) - Atomic session lifecycle, rollback isolation.
- `test_v17_6_4_3_current_evidence_health.py` (5 tests) - Dashboard snapshot evidence freshness, zero-trade health.
- `test_v17_6_4_3_watcher_scheduler_integration.py` (5 tests) - Watcher/Scheduler integration, 0 live exchange orders.
- `test_v17_6_4_3_acceptance_integrity.py` (16 tests) - Master runner self-test, gate validation.
- `test_v17_6_4_2_*` (94 tests) - Persistence status, cycle commit truth, quote authority.
- `test_v17_6_4_1_*` (67 tests) - Feed evidence truth, interval durability, quality transitions.
- `test_v17_6_4_*` (40 tests) - 24/7 calendar coverage, interval producer, observation API.
- `test_v17_6_3_*`, `test_v17_6_2_*`, `test_v17_6_1_*` (45 tests) - CME/Bitget calendars, DST transitions.

### 2.2 Strategy & Execution Engine (V16.x & V17.x)
- `test_v17_5_*` - Opportunity monitor, entry context gates, cost authority.
- `test_v16_39_*` - Snapshot selector, lesson rules, score binding contracts.
- `test_v16_20_*` to `test_v16_28_*` - CG01 production flow, setup correctness, red reproduction suites.
- `test_v16_10_*` to `test_v16_19_*` - HTF structure, pullback correctness, causal OHLC execution.
- `test_v16_1_*` to `test_v16_9_*` - Broker execution, replay ledger, measurement integrity.

### 2.3 Core Financial & Risk Architecture (V10.x to V15.x)
- `test_v15_*` - Scalp orchestrator, liquidity engine, causal entry, quote age parity.
- `test_v14_*` - NY session quotas, three-session context, reconciliation authority.
- `test_v13_*` - Daily scheduler, price plans, ledger idempotency, cadence contracts.
- `test_v12_*`, `test_v11_*`, `test_v10_*` - Multi-month historical replays, RR cost accounting, acceptance matrices.

---

## 3. Environment Pollution Resolution Audit

### Root Cause
During full test discovery, 9 historical test files were identified containing an unsafe environment assignment at module import level:
```python
os.environ["AURUM_DB_PATH"] = ":memory:"
```
When pytest loaded these modules, it permanently mutated `os.environ["AURUM_DB_PATH"]` for the entire OS process. Subsequent tests (such as `test_v10_2_acceptance.py::test_n03`) that relied on `conftest.py`'s `TEST_DB_PATH` file fixture had their database engine re-pointed to transient in-memory databases with `NullPool`, leading to table recreation failures and missing records across 113 tests.

### Remediation
All 9 files were updated to use non-destructive environment defaulting:
```python
os.environ.setdefault("AURUM_DB_PATH", ":memory:")
```
This guarantees:
1. When run in standalone mode, the test defaults safely to in-memory operation.
2. When run inside the full pytest harness, the process-level `TEST_DB_PATH` established by `conftest.py` is respected.
3. Zero test pollution bleed across the entire backend test corpus.
