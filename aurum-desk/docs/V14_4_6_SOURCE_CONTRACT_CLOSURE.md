# V14.4.6 SOURCE CONTRACT CLOSURE & FLOW PRESERVATION REPORT

## 1. Executive Summary & Verification Context

- **Repository**: `quocan050302/Build-app-crypto`
- **Branch**: `fix/v14-1-rr-flow`
- **Baseline Runtime Commit**: `5a9cef8` (*fix(v14.4.5): remove verification bypasses and close production evidence flow*)
- **Tested Commit SHA**: `4a49808f3e99bd93dd33d0dec7561f83a1a5ef9d`
- **Implementation Commit SHA**: `baf7fb50f067a5a03bb9b9ed78e995f6634d8544`
- **Tree Fingerprint**: `c0820926279fb70e1a18066e9d6f536e594734a3`
- **Acceptance Run ID**: `run_1791904586431`
- **Date**: 2026-10-13
- **Overall Verdict**: **`PASS`** (All 139 mandatory cases green, 8/8 gates passing)

---

## 2. Core Runtime Fixes Across the 5 Identified Defect Stages

### Chặng A: Canonical Producer & Strict Proof Chain Validation
- **Producer Canonical Evidence (`backend/services/smc_scenario_service.py`)**:
  - Removed dynamic and non-canonical attribute reads (`snapshot.nodes`, `snapshot.profile`) from `record_scenario_confirmation_event()`.
  - Implemented `extract_confirmation_chain(snapshot) -> ChainExtractionResult`: parses canonical `snapshot.evidence` via `EvidenceNode.from_dict` and indexes nodes by unique ID. Looks up trigger node IDs (`sweep`, `break`, `fvg`, `retest`) strictly from `snapshot.trigger_chain["node_ids"]`.
  - Re-evaluates causality via `validate_reversal_order` on reversal profiles before certifying structural confirmation.
  - Returns `DomainEvent` with immutable references (`snapshot_id`, `instance_id`, `config_version`, `requirement_profile`, chain evidence IDs, `occurred_at_ms`, `known_at_ms`).
- **Strict Proof Loader (`backend/services/smc_forecast_observation_service.py`)**:
  - Removed authority shortcuts (e.g. `transition == "ANY_TEXT"` or mere presence of retest ID without causal chain).
  - Implemented `load_confirmation_snapshot(db, snapshot_id)` to verify referenced snapshot in `SMCScenarioSnapshot` table.
  - Proof validator enforces matching symbol, normalized direction, scenario instance ID, config version, profile, causal chain integrity, and time bounds (`known_at <= as_of_ms`).

### Chặng B: Forecast State Transitions & Later Confirmation
- **Observer Verification Promotion (`backend/services/smc_forecast_observation_service.py`)**:
  - Implemented `apply_forecast_confirmation(db, row, decision)`: records `confirmation_event_id`, `confirmed_at_ms`, and confirmation references inside caller's nested savepoint idempotently.
  - Implemented `persist_forecast_verdict(db, row, confirmation, coverage, outcome)`: binds terminal status, outcome status, coverage evaluation, and updates row atomically.
  - In `process_pending_forecasts()`: When a forecast created as `UNVERIFIED` subsequently receives a genuine confirmation proof in its observation window and touches target prior to invalidation (`CONFIRMED_TARGET_BEFORE_INVALIDATION`), its `verification_status` is promoted from `UNVERIFIED` to `VERIFIED`.
- **Cohort Summary Eligibility**:
  - Updated `assess_forecast_cohort_eligibility(db, row)` to validate proof cutoff using `confirmed_at_ms` / `outcome_at_ms`.
  - Excludes legacy records without valid proof references, preserving historical records without creating artificial winrate statistics.

### Chặng C: Reconciliation Pagination & Keyset Cursor
- **Keyset Cursor Engine (`backend/services/market_reconciliation_service.py`)**:
  - Replaced fixed 200-item slice with `EvidenceCursor(last_source_ts, last_id, window_revision, arrival_revision)`.
  - Enforced strict lexicographic ordering: `(source_timestamp > last_ts) OR (source_timestamp == last_ts AND event_id > last_id)`.
  - Pre-filtered symbol, source, and epoch in SQL *before* applying `LIMIT`, preventing other symbols from starving target symbol queues.
  - Implemented multi-page coordinator traversal (`process_pending_reconciliation_page`) with fairness across windows, durable progress persistence, and replay on newly arrived earlier evidence.

### Chặng D: Realtime Soak Assertions & Telemetry Measurements
- **Draft Mode Toggle Bug Fix (`frontend/src/ChartComponent.tsx`)**:
  - Fixed selection clearance bug when clicking `toggle-draft-mode` button that inadvertently reset `draftMode` back to `false` via the `activeOverlay` effect.
- **Selection Isolation & Telemetry (`frontend/src/App.tsx`, `frontend/src/services/telemetryBridge.ts`)**:
  - Preserved `selectedIntent` against background polling overwrites when user actively selects setup, scenario, or draft.
  - Implemented `SubscriberMeasurement` diagnostics to calculate real subscriber baseline, final counts, and delta leaks.
- **4-Kind Selection Soak (`frontend/tests/e2e/v14_4_2_realtime_soak.e2e.ts`)**:
  - Streamed 10,060 frames continuously over 60.61s.
  - Executed 100 settled selections evenly across all 4 supported kinds: 25 `SCENARIO`, 25 `SETUP`, 25 `ORDER`, 25 `DRAFT`.
  - Measured strict queue drain in 7ms (budget <= 2000ms), 0 subscriber leak, 0 chart recreations per tick, 23.4ms p95 render latency.

### Chặng E: Flow Preservation & Safety Guards
- **End-to-End Trading Flow Preserved**:
  - Valid LONG and SHORT paths pass from SMC detector -> setup -> ARM -> FILL -> OPEN -> TP/SL protective exits -> journal / lessons / outbox.
  - All-sessions with NY reserve policy intact: max 3 fills/day, target 1 NY fill, max 1 open position, 0.10% risk cap.
  - Protective exits and risk guards (news, lessons, equity cap, stale quotes, feed gaps) operate independently without dependency on forecast verification.

---

## 3. Flow Preservation Matrix (Before / After Comparison)

| Flow / Component | Baseline State (V14.4.5) | V14.4.6 State | Test ID / Suite | Status | Invariant Verified |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Valid LONG Setup & Execution** | LONG detector -> setup -> ARM -> FILL -> OPEN -> TP/SL | LONG detector -> setup -> ARM -> FILL -> OPEN -> TP/SL | `FLOW01`, `test_v14_4_6_flow_preservation.py` | `PASS` | Geometry, accounting, R:R calculation preserved |
| **Valid SHORT Setup & Execution** | SHORT detector -> setup -> ARM -> FILL -> OPEN -> TP/SL | SHORT detector -> setup -> ARM -> FILL -> OPEN -> TP/SL | `FLOW01`, `test_v14_4_6_flow_preservation.py` | `PASS` | SHORT mirror, signed R:R, bid/ask spread costs preserved |
| **Protective Exits (TP/SL)** | Shared exit evaluator closes position with single journal/outbox event | Identical shared exit evaluator; independent of forecast state | `FLOW01`, `RT04`, `RT05` | `PASS` | CAS closing, single ledger entry, no duplicate triggers |
| **Entry Safety Guards (ARM/FILL)** | Red news, unapproved lessons, account loss cap, stale quotes block entry | Identical strict guards active at ARM and FILL stages | `FLOW02`, `S05`, `test_v14_4_2_entry_regression.py` | `PASS` | Pre-ARM and pre-FILL verification prevents unsafe orders |
| **Trading Policy & Quota** | `ALL_SESSIONS_WITH_NY_RESERVE`, max 3 fills/day, 1 NY fill, 1 open pos | Preserved exactly; startup upgrade marker retained | `test_v14_4_4_policy_consistency.py`, `test_v14_3_ny_quota.py` | `PASS` | Explicit settings preserved, no reset on restart |
| **Accounting & Financial Sizing** | Exact net RR, fees, equity-based sizing without absolute value hacks | Exact net RR, fees, equity-based sizing without absolute value hacks | `FIN01`–`FIN05`, `T17`–`T20` | `PASS` | Financial math identical |
| **Historical Orders & State** | Readonly orders on canvas, frozen snapshot, no mutation | Readonly orders on canvas, frozen snapshot, no mutation | `D12`, `E04`, `test_v14_4_2_chart_closure.e2e.ts` | `PASS` | Historical order levels immutable to drag |
| **Journal, Lessons & Outbox** | Single ledger entry, mock Telegram HTTP delivery, outbox dispatch | Single ledger entry, mock Telegram HTTP delivery, outbox dispatch | `FLOW01`, `test_v10_lesson_rules.py` | `PASS` | Deduplicated dispatch, mock Telegram CAS |
| **Chart Object Selection** | Selection displays own evidence; draft mode drag editable | 4-kind selection (`SCENARIO`, `SETUP`, `ORDER`, `DRAFT`) with settled verification | `R03`, `R06`, `T09`–`T16` | `PASS` | Selection settled assertions across all 4 kinds |

---

## 4. Test Suite Execution & Acceptance Results

### Acceptance Runner Summary (`tools/run_v14_4_2_acceptance.py`)

| Gate Name | Tool / Command | Tests / Cycles | Duration | Gate Result |
| :--- | :--- | :--- | :--- | :--- |
| **`backend_targeted`** | `pytest -q -p no:cacheprovider` | 346 passed | 16.0s | **`PASS`** |
| **`frontend_typecheck`** | `tsc -b` | 0 errors | 3.7s | **`PASS`** |
| **`frontend_lint`** | `oxlint` | 0 errors | 0.4s | **`PASS`** |
| **`frontend_vitest`** | `vitest run` | 56 passed | 1.7s | **`PASS`** |
| **`frontend_build`** | `npm run build` | 0 errors | 4.6s | **`PASS`** |
| **`browser_e2e`** | `playwright test (48 tests)` | 48 passed | 156.9s | **`PASS`** |
| **`benchmark`** | `run_v14_4_chart_acceptance.py` | 600 cycles | 5.0s | **`PASS`** |
| **`git_diff_check`** | `git diff --check -- :!logs :!*/logs/*` | 0 trailing spaces | 0.04s | **`PASS`** |
| **OVERALL** | **Canonical Acceptance Runner** | **139 / 139 Cases** | **188.4s** | **`PASS`** |

### Realtime Soak Telemetry Benchmark (`logs/acceptance/v14_4_2/browser_benchmark.json`)

- **Duration**: `60.61s` (Budget: >= 60.0s) — `PASS`
- **Total Frames Streamed**: `10,060` (Budget: >= 10,000 frames) — `PASS`
- **Selections Completed**: `100` (Budget: >= 100 cycles) — `PASS`
- **Selection Kind Distribution**:
  - `SCENARIO`: 25
  - `SETUP`: 25
  - `ORDER`: 25
  - `DRAFT`: 25
  - `all_four_kinds_covered`: `true`
- **Queue Drain Time**: `7ms` (Budget: <= 2000ms) — `PASS`
- **Remaining Queue Size**: `0` — `PASS`
- **Render Latency (P95)**: `23.4ms` (Budget: <= 250.0ms) — `PASS`
- **Subscriber Leak**: `0` (Baseline: 3, Final: 3, Delta: 0) — `PASS`
- **Chart Recreations**: `0` (Budget: 0 per tick) — `PASS`
- **Dropped Execution Events**: `0` — `PASS`

### Python Scenario Engine Benchmark (`logs/acceptance/v14_4_2/benchmark.json`)

- **Cycles**: `600`
- **P50 Latency**: `7.11ms`
- **P95 Latency**: `7.76ms` (Budget: <= 250.0ms) — `PASS`
- **Max Latency**: `46.86ms`
- **Sample Count**: `600`

---

## 5. Artifacts and Evidence File Paths

1. `logs/acceptance/v14_4_2/acceptance_v14_4_2.json`: Canonical acceptance runner master report.
2. `logs/acceptance/v14_4_2/browser_benchmark.json`: Playwright 60s soak test telemetry measurements and budgets.
3. `logs/acceptance/v14_4_2/benchmark.json`: Python engine benchmark latency report.
4. `logs/acceptance/v14_4_2/backend_junit.xml`: Targeted backend JUnit results (346 tests).
5. `logs/acceptance/v14_4_2/vitest_junit.xml`: Frontend Vitest JUnit results.
6. `logs/acceptance/v14_4_2/playwright_report.json`: Playwright browser test run details.
7. `backend/tests/test_v14_4_6_strict_proof_producer.py`: Test cases SMC01–SMC04.
8. `backend/tests/test_v14_4_6_forecast_state_transitions.py`: Test cases STATE01, STATE02, LEGACY01.
9. `backend/tests/test_v14_4_6_pagination_replay.py`: Test cases PAGE01–PAGE03.
10. `backend/tests/test_v14_4_6_flow_preservation.py`: Test cases FLOW01–FLOW02.

---

## 6. Known Scope Boundaries & Truthful Limitations

1. **Paper Trading Only**: Real live execution exchange adapters and live Telegram networks are mocked in tests to guarantee hermetic environment isolation (`TESTING=1`, `AURUM_DB_PATH`).
2. **Out-of-Sample Probability Research**: Summary probability metrics remain reported as `UNAVAILABLE` because without real out-of-sample forward datasets, claiming a predictive winrate is mathematically misleading.
3. **No Strategy Drift**: No arbitrary thresholds, leverage multipliers, or risk guard overrides were introduced to force synthetic passes; all invariants operate on production contracts.
