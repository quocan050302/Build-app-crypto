# Aurum V17.1 — Monitor Scope and Chart Fix Implementation Map
**Release:** Aurum V17.1  
**Commit Branch:** `fix/v14-1-rr-flow`  
**Repository:** `quocan050302/Build-app-crypto`  
**Base Commit SHA:** `d7915be226b9b35d48de606f0f48a55511d5c649` (`feat(v17.0): add compact read-only paper opportunity monitor`)  
**Design Principle:** Read-only observation, zero SQL mutations, unified query resolution context, strict multi-run isolation, full filter parity across list/detail/export, canonical chart target DTO hydration with geometry and snapshot threshold preservation.

---

## 1. Architectural Summary & Scope Boundaries

V17.1 resolves the three architectural and data integrity failures identified during the V17.0 monitor evaluation:
1. **Failure M1 (Cross-Run Leak):** Summary and opportunity list operated under disparate scopes. The summary resolved an active observation run while the list fell back to an unconstrained multi-run query.
2. **Failure M2 (Filter Dropped at Detail & Export):** The detail reader and export endpoint dropped the time window (`start_ms`, `end_ms`), `session_id`, and `model_id`. Pagination (`loadNextPage`) recomputed `Date.now()` on every call, causing sliding end boundaries.
3. **Failure M3 (Non-Canonical Chart Focus DTO):** The drawer constructed a synthetic object containing only `{ id, order_id, instrument, direction }`, omitting `planned_entry`, `stop_loss`, `take_profit`, `state`, and `cost_snapshot`. This broke downstream handlers in `App.tsx` and prevented proper chart overlays and risk verification.

### Scope Constraints
- **Zero SQL Mutations:** `db.autoflush = False`, strict read-only execution verified by `SqlMutationWatcher`. No `INSERT`, `UPDATE`, `DELETE`, or DDL statements.
- **No Strategy Alterations:** Zero changes to SMC/ICT logic, POI detection, R:R calculation, news blackout rules, or order execution guards.
- **Compact UI Retained:** Card CTA "Xem cơ hội" in `CG01DiagnosticCard` opens a single master-detail drawer overlay (560-640px desktop, full height 390x844 mobile). No permanent tables or chart clutter.

---

## 2. Function & Route Contracts

### Backend Services (`backend/services/paper_opportunity_monitor_service.py`)

#### `make_monitor_query_fingerprint(query: MonitorQueryContext) -> str`
- Generates a deterministic hash representing the exact query tuple: `(symbol, scope, run_id, model_id, session_id, start_ms, end_ms)`.
- Excludes cursor tokens so that all paginated pages share the same query identity.

#### `resolve_monitor_query_context(db: Session, requested: MonitorQueryContext) -> ResolvedMonitorContext`
- Resolves the requested query against DB observation runs via `find_existing_observation_run`.
- If `requested.run_id` is specified: verifies scope and symbol matching. Returns `FOUND`, `NOT_FOUND`, or `RUN_SCOPE_MISMATCH`.
- If `requested.run_id` is `None`: resolves the active `COLLECTING` or `CREATED` run for the symbol and scope. If no run exists, returns `NOT_CONFIGURED` without querying the full table.
- Binds effective start and end timestamps: `effective_start = run.started_at_ms` if present, else bounded within 31 days.
- Returns immutable `ResolvedMonitorContext(resolution_status, query, effective_run_id, query_key, limitation)`.

#### `build_monitor_scope_predicates(query: MonitorQueryContext) -> list`
- Builds unified SQLAlchemy binary expressions enforcing `run_id`, symbol, half-open time window `[start_ms, end_ms)`, and optional `session_id` and `model_id`.
- Replaces legacy multi-run fallback logic with strict query binding across all queries (counts, grouped stages, and milestones).

#### `build_monitor_chart_focus_context(db, opportunity_id, resolved_query, expected_target_id, expected_kind, expected_revision) -> MonitorChartFocusContext`
- Pure read-only loader for canonical chart focus context.
- Validates that the opportunity exists within the scoped predicates.
- Resolves the verified target (`ORDER` or `SETUP`) from persisted links and models:
  - For `ORDER`: reads `PaperOrder`, preserves `cost_snapshot` (including historical minimum R:R 2.0 snapshot), planned/actual prices, and evidence snapshots. Validates finite geometry.
  - For `SETUP`: reads `WatchSetup`, validates matching version/revision. If current setup version has evolved beyond the opportunity observation without historical snapshot, returns `STALE_TARGET`.
- Returns typed `MonitorChartFocusContext(status, kind, query_key, opportunity_id, target_id, dto, reason_code, message_vi)`.

---

### Backend API Routes (`backend/main.py`)

| Endpoint | Method | Parameters | Description |
| :--- | :--- | :--- | :--- |
| `/api/v1/paper/opportunity-monitor/summary` | `GET` | `symbol`, `scope`, `run_id`, `start_ms`, `end_ms`, `session_id`, `model_id` | Returns summary counts and financial metrics under shared resolved context. |
| `/api/v1/paper/opportunity-monitor/opportunities` | `GET` | `symbol`, `scope`, `run_id`, `start_ms`, `end_ms`, `session_id`, `model_id`, `cursor`, `limit` | Returns paginated list of opportunities isolated to resolved run. |
| `/api/v1/paper/opportunity-monitor/opportunities/{opportunity_id}` | `GET` | `opportunity_id`, `symbol`, `scope`, `run_id`, `start_ms`, `end_ms`, `session_id`, `model_id` | Returns scoped opportunity detail, milestones, and price plan. Returns 404 if outside window. |
| `/api/v1/paper/opportunity-monitor/opportunities/{opportunity_id}/chart-target` | `GET` | `opportunity_id`, `symbol`, `scope`, `run_id`, `expected_target_id`, `expected_kind`, `expected_revision` | Read-only canonical chart target DTO hydration with zero writes. |
| `/api/v1/paper/opportunity-monitor/report/markdown` | `GET` | `symbol`, `scope`, `run_id`, `start_ms`, `end_ms`, `session_id`, `model_id` | Returns Markdown report scoped to exact UI filter window. |

---

### Frontend Components & State (`frontend/src/`)

#### Types (`frontend/src/types/paperOpportunityMonitor.ts`)
- Added `CanonicalPaperOrderDTO` and `CanonicalWatchSetupDTO` preserving exact field contracts.
- Added discriminated union `MonitorChartFocusContext` (`AVAILABLE`, `UNAVAILABLE`, `STALE_TARGET`, `SCOPE_MISMATCH`).
- Added `ResolvedMonitorQuery` and `MonitorQueryParams`.
- Extended `OpportunityMonitorSummary`, `MonitorListPage`, and `OpportunityMonitorDetail` with `query_key` and `run_id`.

#### Time Window & Client Validation (`frontend/src/utils/paperOpportunityText.ts`)
- `buildMonitorTimeWindow(filters, timezone, nowMs)`: Pure helper producing half-open timestamps `[start_ms, end_ms)` in `Asia/Ho_Chi_Minh` timezone with next-day exclusive boundary for custom date ranges.
- `validateMonitorChartFocusContext(result, expected)`: Client-side validation ensuring target ID matching, kind matching, finite prices, and valid LONG/SHORT geometry before delegating to `App.tsx` handlers.

#### Lifecycle Hook (`frontend/src/hooks/usePaperOpportunityMonitor.ts`)
- Uses `buildMonitorTimeWindow` to compute initial window bounds.
- Tracks `resolvedQuery: ResolvedMonitorQuery | null`.
- Propagates full resolved query parameters (`start_ms`, `end_ms`, `session_id`, `model_id`, `run_id`) to `fetchDetail`.
- Pins time window in `loadNextPage` using `resolvedQuery` timestamps instead of shifting `Date.now()`.
- Implements abort controllers and generation counters for overview, pagination, and detail fetches to prevent stale response races.

#### Drawer Overlay (`frontend/src/components/PaperOpportunityMonitorDrawer.tsx`)
- Async `handleFocusChart`: fetches canonical target context via `api.getOpportunityMonitorChartTarget`.
- In-flight state: displays loading spinner ("Đang tải bằng chứng..."), disables button.
- Error handling: if target is unavailable or stale, displays inline error message in drawer, keeps drawer open, and leaves chart selection unchanged.
- On success: closes drawer first, then invokes `onFocusTradeOnChart(dto)` or `onFocusSetupOnChart(dto)` with the full canonical DTO.
- Export handling: passes full filter context to `getOpportunityMonitorReportMarkdown` and names files with the active date range.

---

## 3. Verification & Acceptance Summary

- **Unit & Integration Tests (Backend):** 5/5 passed in `test_v17_1_monitor_scope_and_focus.py` (M1 repro, M2 repro, M3 canonical DTO, setup stale revision, zero SQL mutations).
- **Regression Tests (Backend):** 26/26 monitor tests passed; 46/46 R:R regression tests passed.
- **Unit & Component Tests (Frontend):** 43 test files passed, 475 tests passed (100% pass rate in Vitest).
- **Linter & Typecheck:** 0 errors across 138 files. Vite production build passed.
- **E2E Playwright Browser Integration:**
  - `tests/e2e/v17_0_opportunity_monitor.e2e.ts`: 2 passed (Desktop full observation flow, Mobile 390x844 viewport).
  - `tests/e2e/v16_39_21_rr_final_acceptance.e2e.ts`: 3 passed (Dual-save invariance, LONG/SHORT frozen snapshot, Mobile layout).
