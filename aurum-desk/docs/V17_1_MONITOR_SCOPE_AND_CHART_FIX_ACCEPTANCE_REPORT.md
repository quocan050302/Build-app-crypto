# Aurum V17.1 — Monitor Scope and Chart Fix Acceptance Report
**Date:** 2026-10-22  
**Branch:** `fix/v14-1-rr-flow`  
**Base Commit SHA:** `d7915be226b9b35d48de606f0f48a55511d5c649`  
**Patch Message:** `fix(v17.1): bind monitor query scope and hydrate canonical chart targets`  
**Review Status:** ALL GATES PASSED (100% Tests, Parity Verified, Zero SQL Mutations, Real Browser E2E Confirmed)

---

## 1. Executive Summary & Root Cause Investigation

This acceptance report details the verification of Aurum V17.1, which eliminates cross-run data pollution, filter drop across master-detail/export views, and incomplete chart focus DTOs in the Paper Opportunity Monitor.

### Root Causes & Remediation

| Failure Code | Root Cause in V17.0 | Remediation in V17.1 | Verification Evidence |
| :--- | :--- | :--- | :--- |
| **M1: Disparate Run Scope** | `list_monitor_opportunities` only filtered `run_id` when `query.run_id` was non-null. Summary resolved an active run for financial metrics while the list queried across all historical runs for the symbol. | Implemented `resolve_monitor_query_context` and `build_monitor_scope_predicates`. Both summary and list resolve the active observation run once and strictly filter queries to that single run context. | `test_repro_m1_default_list_isolates_active_run` PASSED. Active-a items isolated; Old-b excluded. |
| **M2: Filter Drop at Detail & Report** | `build_opportunity_monitor_detail` ignored `start_ms`, `end_ms`, `model_id`, `session_id`. Drawer export did not propagate active date filters. Pagination shifted `Date.now()`. | Expanded API parameters and SQL predicates in detail and export routes. Pinned `start_ms` and `end_ms` via `resolvedQuery` for pagination. | `test_repro_m2_detail_outside_window_returns_404` PASSED. Detail outside window rejected. |
| **M3: Incomplete Chart Focus DTO** | `handleFocusChart` constructed a synthetic literal `{ id, order_id, instrument, direction }`, dropping planned prices, stop loss, take profit, state, and cost snapshot. | Added `GET /chart-target` endpoint returning canonical `PaperOrderResponse` or `WatchSetupItem` projection. Validated on client via `validateMonitorChartFocusContext`. | `test_repro_m3_chart_focus_returns_canonical_order_dto` and frontend regression test PASSED. |

---

## 2. Request & Query Parity Matrix

All monitor resources share the exact resolved context:

| Monitor Resource | `symbol` | `scope` | `run_id` | `start_ms` | `end_ms` | `session_id` | `model_id` |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Summary (`/summary`)** | Bound | Bound | Resolved Active | Bound | Bound | Bound | Bound |
| **List Page 1 (`/opportunities`)** | Bound | Bound | Resolved Active | Bound | Bound | Bound | Bound |
| **Detail (`/opportunities/{id}`)** | Bound | Bound | Resolved Active | Bound | Bound | Bound | Bound |
| **Chart Target (`/chart-target`)** | Bound | Bound | Resolved Active | Bound | Bound | Bound | Bound |
| **Markdown Export (`/report/markdown`)** | Bound | Bound | Resolved Active | Bound | Bound | Bound | Bound |
| **Pagination Next Page (`cursor`)** | Bound | Bound | Pinned from P1 | Pinned | Pinned | Bound | Bound |

*Note on Financial Metrics:* Financial metrics in `summary` carry `metric_scope = 'RUN_WINDOW'` and report trades for the resolved run within the observation window.

---

## 3. Canonical Chart Target DTO Comparison

| Field / Attribute | V17.0 Implementation | V17.1 Canonical Implementation | Preservation Rule |
| :--- | :--- | :--- | :--- |
| **Target Kind** | Synthetic string | `ORDER` \| `SETUP` (discriminated) | Exact DB entity kind |
| **Identity (`id`)** | Object literal property | Persisted primary key | Target identity verified |
| **Setup Instance ID** | Undefined / missing | Persisted `setup_instance_id` | Exact instance provenance |
| **Planned Entry** | `undefined` (ORDER) / provisional plan (SETUP) | `2650.0` (finite number) | Exact entry level |
| **Stop Loss** | `undefined` (ORDER) / provisional plan (SETUP) | `2640.0` (finite number) | Exact stop loss level |
| **Take Profit** | `undefined` (ORDER) / provisional plan (SETUP) | `2685.0` (finite number) | Exact target level |
| **Direction** | Inferred from opportunity row | Persisted direction (`LONG` / `SHORT`) | Order direction authoritative |
| **Order State** | Missing | `FILLED`, `ARMED`, `CLOSED`, etc. | Exact lifecycle state |
| **Cost Snapshot** | `undefined` | `{ minimum_snapshot_rr: 2.0, ... }` | Historical threshold preserved |
| **Evidence Snapshots** | Omitted | Preserved IDs (`quality`, `evidence`) | Full chart overlay context |
| **Stale Target Behavior** | Silent fake DTO creation | Returns `STALE_TARGET` / Inline error | Drawer remains open, chart untouched |

---

## 4. Test Verification Evidence

### 4.1 Backend Pytest Suites
- **Command:** `$env:PYTHONPATH="backend;."; & "backend/venv/Scripts/python.exe" -m pytest backend/tests/test_v17_0_opportunity_monitor_api_readonly.py backend/tests/test_v17_0_opportunity_monitor_read_model.py backend/tests/test_v17_0_opportunity_monitor_metrics.py backend/tests/test_v17_1_monitor_scope_and_focus.py backend/tests/test_v16_39_21_e2e_financial_fixtures.py -v`
- **Result:** **31 passed, 1 warning (Starlette deprecation)** in 0.82s.
- **Key Checks:**
  - `test_repro_m1_default_list_isolates_active_run`: PASS.
  - `test_repro_m2_detail_outside_window_returns_404`: PASS.
  - `test_repro_m3_chart_focus_returns_canonical_order_dto`: PASS.
  - `test_chart_focus_setup_and_stale_revision`: PASS.
  - `test_zero_sql_mutations_on_chart_target_endpoint`: PASS (0 mutations recorded via `SqlMutationWatcher`).

### 4.2 Frontend Vitest & Unit Suites
- **Command:** `npm test` (in `aurum-desk/frontend`)
- **Result:** **43 test files passed, 475 tests passed, 0 failures** in 4.61s.
- **Key Checks:**
  - `PaperOpportunityMonitorDrawer.test.tsx` (8 tests): PASS.
    - Verified Failure M3 Regression: `onFocusTradeOnChart` called with full canonical ORDER DTO (`planned_entry`, `stop_loss`, `cost_snapshot`).
    - Verified Stale / Unavailable Target: inline error rendered (`data-testid="monitor-focus-error"`), drawer remains open, chart handler not called.
  - `usePaperOpportunityMonitor.test.tsx` (3 tests): PASS.
    - Verified full query context (`run_id`, `start_ms`, `end_ms`) passed to `getOpportunityMonitorDetail`.
    - Verified `resolvedQuery` populated and exposed.
  - `paperOpportunityText.test.ts` (14 tests): PASS.
    - Verified `buildMonitorTimeWindow` UTC boundary and half-open interval.
    - Verified `validateMonitorChartFocusContext` geometry checks and error classifications.

### 4.3 Frontend Linter & Build
- **Linter (`npm run lint`):** 0 errors, 53 pre-existing legacy warnings in other modules.
- **Build (`npm run build`):** `tsc -b` and `vite build` succeeded with 0 errors in 1.28s.

### 4.4 Playwright Browser Integration
- **Command:** `$env:AURUM_E2E_API_PORT="8899"; $env:AURUM_E2E_WEB_PORT="5299"; npx playwright test tests/e2e/v17_0_opportunity_monitor.e2e.ts`
- **Result:** **2 passed in 13.1s**.
  - `E2E Gate 1-8: Desktop Observation Workflow (Collapsed -> Drawer -> Master/Detail -> Chart Focus -> Report)`: PASSED.
  - `E2E Gate 9: Mobile Viewport (390x844 Fullscreen Layout)`: PASSED.
- **R:R Regression Playwright Test:**
  - **Command:** `$env:AURUM_E2E_API_PORT="8899"; $env:AURUM_E2E_WEB_PORT="5299"; npx playwright test tests/e2e/v16_39_21_rr_final_acceptance.e2e.ts`
  - **Result:** **3 passed in 1.0m** (`V16.39.21-ACCEPTANCE-LONG`, `V16.39.21-ACCEPTANCE-SHORT`, `V16.39.21-MOBILE`).

---

## 5. Zero-Write Safety & Immutability Proof

1. **SQL Listener Verification:** `SqlMutationWatcher` attached to SQLAlchemy engine confirmed 0 `INSERT`, `UPDATE`, `DELETE`, or `REPLACE` statements during summary, list, detail, export, and chart-target reads.
2. **Session Configuration:** All read handlers operate with `db.autoflush = False`.
3. **No Mutating Helpers:** Chart target endpoint does not invoke mutating audit creators (`get_or_create_today_audit`) or evaluation engines (`evaluate_setup_eligibility`).

---

## 6. Git & Hand-Off Status

- **Working Directory:** All changes committed on branch `fix/v14-1-rr-flow`.
- **Target Branch:** `fix/v14-1-rr-flow`.
- **User Edits & Live State:** No user edits outside monitor scope modified; active positions, account settings, and live database preserved.
