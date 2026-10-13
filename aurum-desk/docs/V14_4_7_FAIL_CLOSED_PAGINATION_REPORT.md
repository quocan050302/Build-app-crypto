# V14.4.7 FAIL-CLOSED PROOF & COORDINATOR PAGINATION CLOSURE REPORT

## 1. Executive Summary & Verification Context

- **Overall Status**: **`PASS`** (All 149/149 mandatory cases green, 8/8 gates passing)
- **Repository**: `quocan050302/Build-app-crypto`
- **Branch**: `fix/v14-1-rr-flow`
- **Base / HEAD Commit**: `5d9b8fc34012c93c908fa9242482a080875ec70a` (*docs: record final implementation SHA in V14.4.6 contract closure report*)
- **Code Runtime Baseline**: `baf7fb50f067a5a03bb9b9ed78e995f6634d8544` (*fix(v14.4.6): validate canonical proof chains and complete forecast pagination flow*)
- **Implementation Commit SHA**: `1a2214488695aa642c52d70ebe6ae24822d73747`
- **Tree Fingerprint**: `5143f3286456e77ae790b17cd26c1aebb5698fca`
- **Acceptance Run ID**: `run_1791907840354`
- **Date**: 2026-10-13
- **Preservation Assurance**: Full preservation of SMC detector, entry guards (news/lesson/account/feed/quote/cost/policy), order execution (ARM -> FILL -> OPEN -> TP/SL protective exits -> journal/lessons/outbox), `ALL_SESSIONS_WITH_NY_RESERVE` policy, 3 fills/day UTC+7, single open position, 0.10% risk cap, and immutable chart/accounting contracts.

---

## 2. Root Cause Analysis of Previous Defects

1. **Defect 1 (Proof Fail-Open on Missing Snapshot)**:
   - *Symptom*: When a `scenario.confirmed` event referenced a non-existent `snapshot_id` with fake break/retest IDs, `load_and_validate_confirmation_proof` still returned `is_valid=True` and `create_forecast_snapshot` produced `VERIFIED` status.
   - *Root Cause*: `smc_forecast_observation_service.py` only validated the causal chain *if* snapshot lookup succeeded (`if snap_id:` and `if snap_obj is not None:`). If snapshot lookup failed, the code fell through to an unauthoritative `has_chain = bool(...)` check based solely on string ID presence in the event payload.
2. **Defect 2 (Coordinator CLOSED Window Starvation at 50 Orders)**:
   - *Symptom*: With 51 historical CLOSED orders, running the reconciliation coordinator repeatedly audited only the 50 newest orders; order 51 was never reached.
   - *Root Cause*: `load_pending_reconciliation_windows(limit=50)` returned 50 items and `next_cursor=None` for CLOSED orders. Furthermore, `MarketReconciliationService.process_late_batch` never advanced or persisted window cursor continuation, effectively locking each cycle to the newest 50 orders.
3. **Defect 3 (Evidence Budget Reset & Incomplete Traversal)**:
   - *Symptom*: When a window had >4000 evidence events (exceeding `max_pages=20` with `page_size=200`), event 4001 was never reached across cycles, and the coordinator risked resolving decisions from an incomplete prefix.
   - *Root Cause*: `ev_cursor` was maintained as a local loop variable reset on every coordinator invocation. Without durable persistence of evidence progress, budget exhaustion simply stopped the loop, only to restart from the beginning on the next cycle.

---

## 3. Core Runtime Fixes Implemented

### Stage A: Strict Proof Fail-Closed Loader
- **`backend/services/smc_forecast_observation_service.py`**:
  - Implemented typed structures: `ProofReference` and `ConfirmationSourceResult`.
  - Replaced ad-hoc dictionary parsing with `validate_confirmation_event_schema(event) -> ProofReference`.
  - Replaced optional snapshot lookup with strict `load_confirmation_source(db, ref) -> ConfirmationSourceResult`:
    - Queries authoritative `SMCScenarioSnapshot` via `get_scenario_snapshot(db, snapshot_id)`.
    - Returns `is_valid=False, reason_code="PROOF_SNAPSHOT_NOT_FOUND"` immediately if row is missing.
    - Decodes row using `snapshot_from_row(row)`; returns `is_valid=False, reason_code="CORRUPT_PROOF_SNAPSHOT"` on malformed payloads.
    - Validates metadata consistency between row, decoded snapshot, and event reference (symbol, direction, instance_id, config_version, requirement_profile, evidence_digest).
  - Validates causal trigger chain strictly against decoded snapshot evidence nodes via `validate_referenced_canonical_chain()`: verifies reversal order, checks sweep/break/FVG/retest node existence, and ensures decision cutoff bounds (`known_at_ms <= cutoff_ms`).
  - Recorded detailed rejection reasons in `ForecastObservation.proof_verification_reasons` for complete auditability.
  - Eliminated all fallbacks to event string presence. Missing or invalid snapshots fail-closed to `UNVERIFIED` / `UNCONFIRMED`.

### Stage B: Phased Keyset Window Pagination & Coordinator Continuation
- **`backend/services/market_reconciliation_service.py`**:
  - Defined unified typed `WindowCursor(schema_version, phase, last_time, last_order_id, scan_revision, symbol)` supporting both `"OPEN"` (ascending) and `"CLOSED"` (descending).
  - Updated `load_pending_reconciliation_windows(db, symbol, limit, cursor)`:
    - Queries `limit + 1` rows to determine `has_more` without artificial limit truncation.
    - For CLOSED orders: orders by `closed_at DESC, id DESC`, filtering by `(closed_at < cursor.last_time) | ((closed_at == cursor.last_time) & (id < cursor.last_order_id))`.
    - Returns `WindowPage(items, has_more, next_cursor, read_scope)`.
  - Added durable scan progress storage in `SystemConfig` via `load_scan_progress()` and `save_scan_progress()` with optimistic revision concurrency.
  - Integrated coordinator `MarketReconciliationService.process_late_batch`:
    - Loads persisted `ReconciliationScanProgress`.
    - Traverses `WindowPage` using `page.next_cursor`.
    - Advances scan progress across cycles, allowing 51st and subsequent historical CLOSED orders to be audited across repeated executions.

### Stage C: Evidence Slice Budget & Continuation Across Cycles
- **`backend/services/market_reconciliation_service.py`**:
  - Implemented `CycleBudget(max_windows=50, max_pages=20, max_records=4000)` and `EvidenceProgress`.
  - Created `process_window_slice(db, window, progress, budget, coverage_source) -> WindowSliceResult`:
    - Iterates pages bounded by budget without freezing the event loop.
    - Commits `progress.source_cursor` and accumulated candidate refs atomically in `save_window_progress()`.
    - On budget exhaustion, returns `WindowSliceResult(has_more=True, read_complete=False, decision="PENDING_BUDGET")`, cleanly separating reader completeness from outcome finalization.
    - Prevents false finalization when earlier-relevant records remain unread.
  - Added `invalidate_affected_window_progress(db, window_id, arrival_source_ts)`: when late older evidence arrives (`arrival_source_ts <= progress.scan_cutoff`), window progress is invalidated to ensure complete chronological replay.
  - Pre-filtered evidence queries in SQL (`aggregate_id == window.symbol`) before `LIMIT` to prevent other symbols from starving target symbol pagination.

---

## 4. Before / After Reproduction Matrix

| Scenario / Case ID | Defect / Baseline Behavior (BEFORE) | V14.4.7 Production Behavior (AFTER) | Production Caller & Test Verification |
|---|---|---|---|
| **PROOF01**: Missing Snapshot Record | Event references fake `snapshot_id="nonexistent"`. Loader passes via `has_chain` string fallback. Forecast created as `VERIFIED`. | Loader returns `is_valid=False, reason="PROOF_SNAPSHOT_NOT_FOUND"`. Forecast created as `UNVERIFIED`. Observer ignores confirmation. | `smc_forecast_observation_service.py` -> `create_forecast_snapshot` & `process_pending_forecasts`. Tested: `test_v14_4_7_proof_fail_closed.py::test_case_proof01_missing_snapshot_record` |
| **PROOF02**: Corrupt Payload / Missing Fields | Malformed JSON or missing snapshot ID causes uncaught errors or unintended defaults. | Schema validator returns `is_valid=False, reason="INVALID_PROOF_SCHEMA"`. Corrupt row returns `"CORRUPT_PROOF_SNAPSHOT"`. System fails closed gracefully. | `validate_confirmation_event_schema` & `load_confirmation_source`. Tested: `test_v14_4_7_proof_fail_closed.py::test_case_proof02_no_snapshot_id_and_corrupt_row` |
| **PROOF03**: Metadata / Reference Mismatch | Genuine snapshot for SHORT certifying event payload claiming LONG or mismatched instance. | Validates snapshot against event and forecast binding: direction, symbol, instance, config, profile, and digest mismatches strictly rejected. | `validate_event_source_forecast_binding`. Tested: `test_v14_4_7_proof_fail_closed.py::test_case_proof03_mismatched_referenced_snapshot` |
| **PROOF04**: Genuine Producer Chain | Valid scenario cycle produces canonical snapshot with authentic reversal chain. | Authoritative snapshot loaded, evidence nodes verified against trigger chain, reversal order checked, returns `is_valid=True`, forecast certified `VERIFIED`. | `evaluate_scenario_cycle` -> `record_scenario_confirmation_event`. Tested: `test_v14_4_7_proof_fail_closed.py::test_case_proof04_genuine_producer_integration` |
| **COHORT01**: Fake Proof Cohort Exclusion | Unverified or forged events included in cohort winrate calculations. | Fake / missing snapshot records excluded from cohort (`is_valid=False`). Denominator remains `None` when empty. No artificial winrate. | `assess_forecast_cohort_eligibility` & `build_forecast_cohort_summary`. Tested: `test_v14_4_7_proof_fail_closed.py::test_case_cohort01_forged_and_legacy_statuses` |
| **WINDOW01**: 51 CLOSED Orders | 51 closed orders in DB. Coordinator runs repeatedly, always processes newest 50 orders; order 51 never audited. | Coordinator traverses `WindowCursor(phase="CLOSED")`. Cycle 1 audits orders 1..50; Cycle 2 resumes from `next_cursor` and audits order 51. | `MarketReconciliationService.process_late_batch`. Tested: `test_v14_4_7_coordinator_pagination.py::test_case_window01_51_closed_via_actual_coordinator` |
| **WINDOW02**: 101 Mixed Orders & Tie-Keys | Keyset cursor fails or loops infinitely when multiple orders share identical `closed_at` timestamps. | Composite ordering `closed_at DESC, id DESC` handles timestamp ties deterministically. All 101 orders audited without duplicates. | `load_pending_reconciliation_windows`. Tested: `test_v14_4_7_coordinator_pagination.py::test_case_window02_101_mixed_and_tie_keys` |
| **WINDOW03**: Wakeup on Late Evidence | Late evidence arriving for older order outside top 50 ignored or lost. | `register_affected_reconciliation_tasks` flags affected window. Next cycle schedules and audits older window without scanning full history. | `register_affected_reconciliation_tasks`. Tested: `test_v14_4_7_coordinator_pagination.py::test_case_window03_relevant_old_history_wakeup` |
| **EVIDENCE01**: Budget 4001 Events | 4001 evidence records. Coordinator hits 20-page limit (4000 records), resets cursor locally; record 4001 never reached. | Cycle 1 processes 4000 records, persists `EvidenceProgress`, returns `has_more=True`. Cycle 2 resumes from cursor, reaches record 4001 and resolves terminal state. | `process_window_slice` & `process_late_batch`. Tested: `test_v14_4_7_coordinator_pagination.py::test_case_evidence01_default_budget_4001` |
| **EVIDENCE02**: Small Budget & Restart | Injected small budget (e.g. 2 pages) loses progress or restarts from page 1 on service restart. | Progress persisted to `SystemConfig`. Restarting service / new DB session resumes exactly from `last_key` without duplicate work. | `save_window_progress` & `load_window_progress`. Tested: `test_v14_4_7_coordinator_pagination.py::test_case_evidence02_small_injected_budget_and_restart` |
| **EVIDENCE03**: Late Older Evidence Arrival | Evidence with timestamp earlier than cursor arrives after cursor advanced. | `invalidate_affected_window_progress` detects `source_ts <= scan_cutoff` and resets progress for affected window, guaranteeing causal replay. | `invalidate_affected_window_progress`. Tested: `test_v14_4_7_coordinator_pagination.py::test_case_evidence03_late_older_source_timestamp` |
| **EVIDENCE04**: Symbol & Pre-LIMIT Filters | Evidence from multiple symbols (e.g. BTC, ETH) starves XAU window quota. | SQL pre-filter `aggregate_id == window.symbol` applied before `LIMIT`. XAU quota dedicated exclusively to XAU evidence. | `load_persisted_window_evidence`. Tested: `test_v14_4_7_coordinator_pagination.py::test_case_evidence04_symbol_and_cutoff_filters` |

---

## 5. Acceptance Test Suite Results

The complete canonical acceptance runner (`tools/run_v14_4_2_acceptance.py`) was executed on the working tree:

```
============================================================
=== V14.4.2 ACCEPTANCE REPORT ===
  backend_targeted       PASS (357 tests)
  frontend_typecheck     PASS
  frontend_lint          PASS
  frontend_vitest        PASS
  frontend_build         PASS
  browser_e2e            PASS (48 tests)
  benchmark              PASS
  git_diff_check         PASS
  OVERALL                PASS
============================================================
```

### Detailed Breakdown of Gates:
1. **`backend_targeted`**: **`PASS`**
   - 41 targeted test files executed, **357 tests passed**, 0 failed, exit code 0.
   - Includes new dedicated suites:
     - `tests/test_v14_4_7_proof_fail_closed.py` (5/5 PASS)
     - `tests/test_v14_4_7_coordinator_pagination.py` (6/6 PASS)
2. **`frontend_typecheck`**: **`PASS`**
   - TypeScript compiler (`tsc -b`), exit code 0.
3. **`frontend_lint`**: **`PASS`**
   - Oxlint: 0 errors, 29 warnings, exit code 0.
4. **`frontend_vitest`**: **`PASS`**
   - Vitest component tests, exit code 0.
5. **`frontend_build`**: **`PASS`**
   - Production Vite build, exit code 0.
6. **`browser_e2e`**: **`PASS`**
   - Playwright browser E2E: **48 expected tests passed**, 0 failed, 0 skipped, 0 flaky, exit code 0.
7. **`benchmark`**: **`PASS`**
   - Chart p95 render latency: **7.839ms** (budget <= 50ms).
   - Realtime soak: **10,061 frames / 60s**, 100 settled selections across all 4 kinds, 0 subscriber leak, p95 commit proxy: **23.6ms** (budget <= 250ms).
8. **`git_diff_check`**: **`PASS`**
   - Zero trailing whitespace or merge conflict markers, exit code 0.

---

## 6. Flow & Policy Preservation Matrix

| Dimension | Policy / Invariant | Status | Verification Detail |
|---|---|---|---|
| **Trading Policy** | `ALL_SESSIONS_WITH_NY_RESERVE` | **PRESERVED** | Policy intact; Asia/Europe allowed; NY reserve slot maintained. |
| **Daily Fills** | Max 3 actual fills / day UTC+7 | **PRESERVED** | Quota counters and daily caps strictly enforced. |
| **Position Limit** | Exactly 1 open position | **PRESERVED** | Single active position concurrency invariant preserved. |
| **Risk Cap** | Max 0.10% risk per trade | **PRESERVED** | Financial sizing and risk formula unchanged. |
| **SMC Detector** | Detection -> Setup -> ARM -> FILL -> Exits | **PRESERVED** | Geometry, R-multiples, entry guards, and protective exits intact. |
| **Chart UI** | Selected Chart Header / Drawer / DRAFT mode | **PRESERVED** | Read-only order view, DRAFT drag mode, and canonical plan intact. |
| **Durable Audit** | Journal / Lessons / Telegram Outbox | **PRESERVED** | Single ledger entry, CAS close, and idempotent notification intact. |

---

## 7. Artifacts & Run Identity

- Acceptance Report File: `logs/acceptance/v14_4_2/acceptance_v14_4_2.json`
- Backend JUnit XML: `logs/acceptance/v14_4_2/backend_junit.xml`
- Vitest JUnit XML: `logs/acceptance/v14_4_2/vitest_junit.xml`
- Browser Playwright Report: `logs/acceptance/v14_4_2/playwright_report.json`
- Benchmark Output: `logs/acceptance/v14_4_2/benchmark.json`
