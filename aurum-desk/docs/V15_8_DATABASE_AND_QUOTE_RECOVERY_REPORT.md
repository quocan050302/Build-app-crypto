# V15.8 DATABASE FAILURE RECOVERY AND CANONICAL EXECUTION QUOTE VALIDATION REPORT

- **Repository:** `quocan050302/Build-app-crypto`
- **Branch:** `fix/v14-1-rr-flow`
- **Baseline SHA:** `b1236b87ef6a95109d259093639ecc518a34715d`
- **Version:** V15.8
- **Date:** 2026-10-15

---

## 1. Executive Summary & Defect Root Causes

### Defect 1: Lifecycle Persistence Failure Poisoned SQLAlchemy Session
- **Mechanism in Baseline:** When `stage_hypothesis_lifecycle()` or `stage_hypothesis_status_transition()` encountered a database flush failure (e.g., real `IntegrityError` due to a unique key conflict or constraint violation), the underlying SQLAlchemy session was invalidated and placed into a pending-rollback state (`session.is_active is False`).
- **Uncontrolled Cascading Queries:** In baseline V15.7, the caller did not halt immediately after the persistence failure. Instead, it proceeded to execute downstream database queries, such as `risk_settings_service.get_settings(db)` and `crud.get_or_create_today_audit(db)`. These queries immediately raised `sqlalchemy.exc.PendingRollbackError: This Session's transaction has been rolled back due to a previous exception during flush`.
- **System Impact:** The uncaught exception broke cycle execution, leaked poisoned session state across requests, and left in-memory caches and watch setups in an indeterminate state.

### Defect 2: Unprovenanced & Stale Quote Acceptance in Arming & Cycle Paths
- **Mechanism in Baseline:** In `_arm_paper_candidate()` and `evaluate_cycle()`, incoming quotes passed as raw dictionaries or objects with `hasattr(quote, "is_valid")` were accepted without strict timestamp provenance validation.
- **Spoofed / Default Freshness:** Legacy dictionary quotes could supply `freshness_sec=0.0` or omit `server_time` and `observed_at` entirely. As a result, a 999-second stale quote was treated as valid, allowing `_arm_paper_candidate()` to mutate `WatchSetup` to `READY` and arm paper orders against completely expired market prices.
- **System Impact:** Execution entries were armed against phantom prices, exposing paper and live execution models to high-friction, out-of-market fills.

---

## 2. Before & After Reproduction

### Defect 1: Real Database Flush Failure Recovery
#### Before (Baseline V15.7):
```python
# Writer flush triggers IntegrityError
stage_hypothesis_lifecycle(db, "XAUUSDT", hyp, now_ms=t0) # -> IntegrityError on db.flush()
# evaluate_cycle continues:
risk_settings_service.get_settings(db) # -> CRASH: sqlalchemy.exc.PendingRollbackError
```
Result: `PendingRollbackError` propagated out of `evaluate_cycle()`, crashing the cycle runner and leaving the session un-rolled-back.

#### After (Patched V15.8):
```python
# Staging loop detects LifecycleStageResult.FAILED or not db.is_active:
# Immediately aborts and executes _finalize_failed_cycle()
res = orch.evaluate_cycle(db, "XAUUSDT", ...)
```
Result:
- Zero queries executed on poisoned session (`risk_settings_service` and `crud.get_or_create_today_audit` call counts == 0).
- `_arm_paper_candidate()` is NOT invoked.
- `db.rollback()` executed cleanly.
- `res["status"] == "DEGRADED"`, `res["transaction_status"] == "FAILED"`, `res["rollback_status"] == "ROLLED_BACK"`.
- In-memory active hypothesis cache is cleared; `/api/scalp/hypotheses` returns `data_quality: "DEGRADED"`.
- Subsequent cycle on the same session runs cleanly without rollback errors.

### Defect 2: Execution Quote Validation & Provenance
#### Before (Baseline V15.7):
```python
stale_quote = {"bid": 2620.0, "ask": 2620.5, "last": 2620.25, "is_valid": True, "server_time": 1000, "observed_at": 1000}
orch._arm_paper_candidate(db, cand, now_ms=1_000_000, quote=stale_quote, ...)
```
Result: Order armed! `WatchSetup` state mutated to `ARMED`, `PaperOrder` persisted in database despite quote being 999s stale.

#### After (Patched V15.8):
```python
vq = normalize_execution_quote(stale_quote, now_ms=1_000_000)
# vq.is_valid == False, vq.is_stale == True, vq.rejection_code == "TICKER_STALE"
order = orch._arm_paper_candidate(db, cand, now_ms=1_000_000, quote=stale_quote, ...)
```
Result:
- Returns `None` immediately.
- `WatchSetup` count in DB == 0.
- `SMCScenarioSnapshot` count in DB == 0.
- `PaperOrder` count in DB == 0.
- No phantom orders armed.

---

## 3. Production Code Implementation Details

### 1. `backend/services/quote_validator.py`
- **Robust Timestamp Parsing:** Updated `QuoteValidator.validate_ticker()` to strictly parse and validate timestamps (`server_time`, `observed_at`, `exchange_ts_ms`, `received_at_ms`). Handles strings, booleans, and negative numbers safely without throwing unhandled exceptions.
- **Price Sanity Checks:** Added non-finite price validation for `last` in addition to `bid` and `ask` (`NON_FINITE_QUOTE`).
- **`normalize_execution_quote(raw_quote, *, now_ms, max_age_sec=None) -> ValidatedQuote`:**
  * Handles `None`: returns `ValidatedQuote(is_valid=False, rejection_code="FEED_DISCONNECTED")`.
  * Handles `ValidatedQuote`: re-evaluates freshness against `now_ms`, propagates original source rejections.
  * Handles `CanonicalQuote`: re-evaluates freshness against `now_ms`, maps exchange and observed timestamps.
  * Handles `dict`: maps aliases (`server_time_ms`, `exchange_ts_ms`, `received_at_ms`), re-evaluates freshness against `now_ms`. Does NOT mutate input dict. Does NOT fabricate timestamps when provenance is missing.
  * Handles unsupported types: returns `ValidatedQuote(is_valid=False, rejection_code="MALFORMED_QUOTE")`.

### 2. `backend/services/scalp_watcher_orchestrator.py`
- **Cycle Outcome Tracking:** Added `self._latest_cycle_outcome: Dict[str, Dict[str, Any]]` and `get_latest_cycle_outcome(symbol)` to report accurate status to API consumers.
- **Immediate Failure Abort:**
  * In `evaluate_cycle()` staging loop and expiry loop: if `res == LifecycleStageResult.FAILED` or `not getattr(db, "is_active", True)`, aborts immediately and delegates to `_finalize_failed_cycle()`.
- **`_finalize_failed_cycle()` Method:**
  * Safely executes `db.rollback()` in a guarded try-except block, setting `rollback_status` to `"ROLLED_BACK"` or `"ROLLBACK_FAILED"`.
  * Sets `cycle_authority.write_status = "FAILED"`.
  * Clears `self._active_hypotheses[symbol]` to prevent stale cache contamination.
  * Records `_latest_cycle_outcome[symbol] = {"status": "DEGRADED", "reason": "DB_TRANSACTION_FAILED"}`.
  * Returns a structured degraded response dictionary without executing any further queries on the database.
- **Arm-Boundary Quote Defense:**
  * In `_arm_paper_candidate()`: places `normalize_execution_quote(quote, now_ms=now_ms)` BEFORE any database query or mutation.
  * If quote is invalid or stale, logs diagnostic reason and returns `None` immediately, leaving database completely untouched.

### 3. `backend/main.py`
- **Hypotheses Endpoint Data Quality:**
  * Updated `/api/scalp/hypotheses`: checks `scalp_watcher_orchestrator.get_latest_cycle_outcome(symbol)`.
  * When latest cycle failed or degraded, responds with `data_quality: "DEGRADED"` instead of falsely reporting `HEALTHY`.

---

## 4. Test Verification & Acceptance Results

### New Test Suites
1. **`backend/tests/test_v15_8_database_failure_recovery.py` (15 Tests - ALL PASSED):**
   - Real `IntegrityError` flush recovery without `PendingRollbackError`.
   - Clean session recovery for subsequent queries after rollback.
   - Zero queries between poison and rollback (verifying `risk_settings_service` and `crud.get_or_create_today_audit` are NOT called).
   - `_arm_paper_candidate()` is NOT called when transaction fails.
   - `WatchSetup` and snapshot remain unmutated.
   - Staged data before failure is completely rolled back.
   - Expire path real `IntegrityError` recovers cleanly.
   - Writer returning `FAILED` outcome aborts cycle consistently.
   - Writer `BLOCKED` outcome remains isolated without global rollback.
   - Commit failure triggers rollback and degrades gracefully.
   - Rollback failure diagnostic reports honestly without crashing.
   - Cache cleared after DB failure.
   - API reports `DEGRADED` instead of false `HEALTHY`.
   - Next cycle recovers cleanly on same session.
   - Existing committed orders remain untouched across failure cycles.

2. **`backend/tests/test_v15_8_execution_quote_validation.py` (21 Tests - ALL PASSED):**
   - Exact 999s stale quote rejected (`TICKER_STALE`).
   - Missing timestamps rejected (`TIME_UNAVAILABLE`).
   - Fresh quote accepted with accurate timestamps.
   - `ValidatedQuote` aging verified.
   - `CanonicalQuote` aging verified.
   - Spoofed freshness rejected (`freshness_sec=0.0` on old timestamp recalculated and rejected).
   - Input dictionaries not mutated.
   - Threshold boundary tests (14.9s valid, 15.0s valid, 15.1s stale).
   - Future timestamp rejected (`FUTURE_TIMESTAMP`).
   - Non-finite price rejected (`NON_FINITE_QUOTE`).
   - Inverted spread rejected (`INVERTED_SPREAD`).
   - Zero and negative price rejected (`ZERO_OR_NEGATIVE_PRICE`).
   - Timestamp aliases supported.
   - Unsupported types rejected (`FEED_DISCONNECTED`, `MALFORMED_QUOTE`).
   - Direct arm with 999s stale quote returns `None` with zero DB mutations.
   - Direct arm with missing timestamp returns `None`.
   - Direct arm with fresh quote creates armed `PaperOrder`.
   - `evaluate_cycle()` causal LONG with stale quote blocks entry.
   - `evaluate_cycle()` causal SHORT with stale quote blocks entry.
   - `evaluate_cycle()` causal LONG with fresh quote arms order.
   - `evaluate_cycle()` causal SHORT with fresh quote arms order.

### Full Targeted Acceptance Suite
- Test runner: `tools/run_v14_4_2_acceptance.py --skip-e2e`
- Total Targeted Test Files: **81 files** (expanded from 79 to include both V15.8 test files)
- Status: **ALL 81 TARGETED TEST SUITES PASSED (0 FAILURES)**
- Frontend verification: Typecheck, Lint, Vitest, and Build all passing.

---

## 5. Compliance & Architectural Guardrails Confirmation
- [x] Zero changes to SMC liquidity, swing, dealing range, or FVG algorithms.
- [x] Zero changes to risk allocation, capital, margin mode, or leverage calculations.
- [x] Zero changes to SL/TP orientation or tick/buffer offsets.
- [x] Zero mock success to bypass database persistence testing (real SQLite `IntegrityError` verified).
- [x] Zero external network or exchange calls in automated suites.
- [x] All 81 targeted test suites passing cleanly.
