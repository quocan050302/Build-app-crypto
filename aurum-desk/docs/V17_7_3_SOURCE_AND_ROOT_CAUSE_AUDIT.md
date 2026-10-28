# AURUM DESK V17.7.3 — SOURCE AND ROOT CAUSE AUDIT
**Database Safety Isolation, ATR Provenance Authority & Release Gate Truthfulness**  
**Repository:** `quocan050302/Build-app-crypto`  
**Branch:** `feat/v17-7-adaptive-structural-stop-loss`  
**Status:** `VERIFIED_FROM_SOURCE`

---

## 1. Executive Summary

AURUM DESK V17.7.3 is a corrective engineering release following V17.7.2. While V17.7.2 resolved core stop loss evidence checks and missing ATR fail-closed behaviors, source code audits identified critical safety and truthfulness issues in test database isolation, ATR provenance handling, concurrency verification, and acceptance gate reporting.

This document details the exact root causes, call hierarchies, failure mechanisms, affected tests, and remediations across all confirmed findings.

---

## 2. Confirmed Defect Root Cause Analysis

### Defect V173-P0-01: Test Database Isolation Not Guaranteed for Arbitrary Explicit Paths
- **Severity:** CRITICAL (P0)
- **Primary Source Files:** `backend/database.py`, `backend/tests/conftest.py`
- **Root Cause:**
  In V17.7.2, `assert_test_database_isolated(db_path)` only validated that `db_path != RUNTIME_DEFAULT_DB_PATH` and that `"aurum_desk.db"` was not in `db_path.name`. If an operator or script provided `AURUM_DB_PATH=/tmp/trading_live_copy.db` or `AURUM_TEST_DB_PATH=/path/to/my_test.db`, or used path traversal (`../`) or symlinks, the assertion returned `True`. Destructive test fixtures (e.g., `db.query(PaperOrder).delete()`) could execute against arbitrary databases.
- **Failure Mode:**
  Destructive operations could wipe tables in staging, copy, or backup databases if misconfigured via environment variables or CLI options.
- **Remediation Implemented:**
  1. Created a strict, canonical test-owned root system with `.aurum_test_owned` marker metadata via `get_or_create_test_root()`.
  2. Implemented `register_trusted_test_root()` to register pytest-managed temporary bases (`basetemp`).
  3. Enforced strict path containment via `t_path.is_relative_to(root)` after fully resolving symlinks, traversals, and URI schemes.
  4. Made `run_schema_migrations` fail-closed if target database is not test-isolated.
  5. Implemented tests `DBS-01` through `DBS-15` in `test_v17_7_database_safety.py`.

---

### Defect V173-P0-02: Claimed Full Backend Regression Was Selective
- **Severity:** CRITICAL (P0)
- **Primary Source Files:** `tools/run_v17_7_acceptance.py`
- **Root Cause:**
  Gate G16 in the acceptance runner executed `pytest backend/tests/test_v17_*.py -q`, which only collected tests prefixed with `test_v17_`. It excluded earlier architectural test suites (e.g., `test_migration_runtime_upgrade.py`, `test_news_service.py`, `test_realtime_execution_worker.py`, `test_smc_engine.py`, etc.). The artifact recorded `G16_BACKEND_FULL_PYTEST = PASS`, misrepresenting partial test coverage as complete backend verification.
- **Failure Mode:**
  Silent regressions in non-V17 modules could slip through release acceptance unnoticed.
- **Remediation Implemented:**
  1. Upgraded acceptance gate G20 to execute the full test directory: `python -m pytest backend/tests -q`.
  2. Added dynamic output parsing for total collected, passed, failed, and skipped tests.
  3. Ensured discovery covers all 4,000+ tests across the backend suite.

---

### Defect V173-P1-01 & V173-P1-02: Weighted ATR Buffer Masquerading as Raw ATR
- **Severity:** HIGH (P1)
- **Primary Source Files:** `backend/services/stop_placement_service.py`, `backend/services/stop_placement_types.py`
- **Root Cause:**
  `compute_stop_buffer()` calculates buffer components by multiplying raw ATR by multipliers:
  `components["atr_m1"] = policy.atr_m1_multiplier * actual_atr_m1`.
  `plan_structural_stop()` had a fallback pathway that accessed `buffer_result.components["atr_m1"]` and treated it as raw M1 ATR. For example, with Raw ATR M1 = 3.00 and Multiplier = 0.25, the buffer component is 0.75. When used as raw ATR, the distance-to-ATR ratio was calculated against 0.75 instead of 3.00, distorting metrics by 400% and allowing unverified buffer scalars to bypass volatility gate checks.
- **Failure Mode:**
  Invalid volatility evidence could satisfy entry gates, and distance-to-ATR ratios were distorted.
- **Remediation Implemented:**
  1. Eliminated the `buffer_result.components["atr_m1"]` fallback in `plan_structural_stop`.
  2. Added explicit `raw_atr_m1` and `raw_atr_m5` fields to `StopBufferResult`.
  3. Extended `StopATRResult` with authoritative provenance metadata: `symbol`, `source`, `source_candle_count`, `last_closed_candle_timestamp`, `calculated_as_of`, `decision_cutoff`.
  4. In `compute_stop_atr()`, strictly validated symbol matching, excluded unclosed bars (`t_close > as_of_ms`), and rejected non-finite or negative values.
  5. Enforced that an explicit `status=UNAVAILABLE` in `atr_m1_obj` cannot be overridden by raw scalar fallbacks.
  6. Implemented tests `ATR-01` through `ATR-20` in `test_v17_7_atr_provenance.py`.

---

### Defect V173-P1-03: Concurrency Tests Did Not Exercise Production ARM Boundary
- **Severity:** HIGH (P1)
- **Primary Source Files:** `backend/services/scalp_watcher_orchestrator.py`, `backend/tests/test_v17_7_adaptive_stop_loss.py`
- **Root Cause:**
  Previous concurrent tests inserted duplicate `PaperOrder` records with the same primary key from separate threads. While this tested SQLite's unique constraint, it did not exercise `ScalpWatcherOrchestrator._arm_paper_candidate()`. In `_arm_paper_candidate()`, the order creation was guarded by `serialize_entry_mutation` lock, but was not explicitly committed before releasing the lock, allowing a racing thread to read an uncommitted count.
- **Failure Mode:**
  Under high-frequency concurrent evaluation loops, two threads could evaluate and attempt to ARM duplicate orders before the database transaction had committed.
- **Remediation Implemented:**
  1. Added explicit `db.commit()` inside `_arm_paper_candidate()` within the `serialize_entry_mutation` lock block before returning the armed order.
  2. Rewrote `test_ce_16` to launch two threads with `threading.Barrier(2)` invoking `_arm_paper_candidate()` on the identical opportunity simultaneously.
  3. Verified that exactly 1 durable order is written to SQLite and both threads receive the same authoritative order ID.

---

### Defect V173-P1-04 & V173-P1-05: Acceptance Verdict Logic Coupled & Fragile
- **Severity:** HIGH (P1)
- **Primary Source Files:** `tools/acceptance_verdict.py`, `tools/run_v17_7_acceptance.py`
- **Root Cause:**
  The acceptance verdict was calculated in-line in `run_v17_7_acceptance.py` with ad-hoc conditionals. Test `test_ce_19` built a local dictionary and evaluated a mock verdict, never exercising the production runner's logic. Test `test_ce_20` verified git SHA length (40 hex chars) rather than proving that `results["source_sha"]` matched the tested git commit.
- **Failure Mode:**
  A runner could produce a false `ACCEPTED_FOR_PAPER_OPT_IN` if a mandatory gate disappeared from the dictionary or if an exit code contradicted `status: PASS`.
- **Remediation Implemented:**
  1. Extracted `determine_release_verdict()` and `MANDATORY_GATES_V17_7_3` into `tools/acceptance_verdict.py`.
  2. Made verdict fail-closed: missing gates, non-PASS status, exit code != 0, or failed test counts immediately return `BLOCKED`.
  3. Refactored `test_ce_19` to directly test `determine_release_verdict` with mutation injection.
  4. Refactored `test_ce_20` to verify artifact `source_sha` against actual git HEAD.

---

## 3. Function & File Remediation Matrix

| Defect ID | Target File | Target Function / Symbol | Changes Made |
|---|---|---|---|
| V173-P0-01 | `backend/database.py` | `assert_test_database_isolated` | Strict containment check against `_CURRENT_TEST_ROOT` and `_TRUSTED_TEST_ROOTS` |
| V173-P0-01 | `backend/database.py` | `get_or_create_test_root` | Auto-generate unique test root directory with `.aurum_test_owned` marker |
| V173-P0-01 | `backend/database.py` | `run_schema_migrations` | Guard against unsafe database before running schema updates |
| V173-P0-01 | `backend/tests/conftest.py` | `register_test_tmp_path_root` | Register pytest `basetemp` with marker to allow isolated `tmp_path` |
| V173-P1-01 | `backend/services/stop_placement_types.py` | `StopBufferResult` | Added `raw_atr_m1` and `raw_atr_m5` fields |
| V173-P1-01 | `backend/services/stop_placement_service.py` | `plan_structural_stop` | Eliminated fallback reading scaled `components["atr_m1"]` |
| V173-P1-02 | `backend/services/stop_placement_types.py` | `StopATRResult` | Added provenance fields: symbol, source, candle count, timestamps |
| V173-P1-02 | `backend/services/stop_placement_service.py` | `compute_stop_atr` | Enforced symbol match, unclosed bar filtering, future candle exclusion |
| V173-P1-03 | `backend/services/scalp_watcher_orchestrator.py` | `_arm_paper_candidate` | Added `db.commit()` inside `serialize_entry_mutation` lock |
| V173-P1-04 | `tools/acceptance_verdict.py` | `determine_release_verdict` | Extracted pure, fail-closed release verdict resolution |
| V173-P1-05 | `backend/tests/test_v17_7_adaptive_stop_loss.py` | `test_ce_19`, `test_ce_20` | Real verdict injection tests and git SHA parity verification |
| V173-P0-02 | `tools/run_v17_7_acceptance.py` | Gate 20 | Changed command to `python -m pytest backend/tests -q` |
