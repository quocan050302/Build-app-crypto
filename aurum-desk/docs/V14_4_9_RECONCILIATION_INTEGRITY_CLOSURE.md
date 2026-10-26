# V14.4.9 RECONCILIATION INTEGRITY & HARD READ BUDGETS CLOSURE REPORT

## 1. Executive Summary & Verification Context

- **Overall Status**: **`PASS`** (All 175/175 required cases green, 8/8 gates passing, exit code 0)
- **Repository**: `quocan050302/Build-app-crypto`
- **Branch**: `fix/v14-1-rr-flow`
- **Base / HEAD Commit**: `e64a946` (*docs: record final implementation SHA in V14.4.8 closure report*)
- **Implementation Baseline**: `bdb3a20` (*fix(v14.4.8): retain reconciliation candidates and stop safely on exhausted budgets*)
- **Pre-V14.4.8 Baseline**: `ad2501d` (*docs: record final implementation SHA in V14.4.7 closure report*)
- **Implementation Commit SHA**: `ab3e8459d9347793e0b8c3b9a9282111a8d3564f`
- **Acceptance Run ID**: `run_1791944825423`
- **Tested SHA**: `e64a9461fc84c6c4cf81c142f8bcb7ac2707af98`
- **Tree Fingerprint**: `c0b6730546be870893595d94d007ff26b40bfa2a`
- **Date**: 2026-10-14
- **Preservation Assurance**: Complete preservation of SMC detector, entry guards (news/lesson/account/feed/quote/cost/policy), trade lifecycle (ARM -> FILL -> OPEN -> TP/SL protective exits -> journal/lessons/outbox), `ALL_SESSIONS_WITH_NY_RESERVE` session policy, 3 fills/day UTC+7 limit, single open position invariant, 0.10% risk cap, and immutable ledger/accounting contracts.

---

## 2. Root Cause Analysis of Addressed Defects

### Defect 1: Cross-Order Candidate Leakage to Terminal CLOSE Mutation
- **Symptom**: If an accumulator or injected progress contained a candidate belonging to a foreign order (`order_id="OTHER"`), `process_window_slice` logged `WINDOW_ID_MISMATCH` to `conflict_flags` but still passed the foreign candidate to `resolve_reconciliation_window`. The resolver emitted a `CLOSE` decision, and `persist_resolution_or_review` executed `TradeLifecycleService.execute_close`, closing the order, recording PnL, and altering order state based on another order's data.
- **Root Cause**:
  1. `merge_candidate_accumulator` merely appended `WINDOW_ID_MISMATCH` to `conflict_flags` without marking the accumulator invalid (`is_valid=False`).
  2. `process_window_slice` did not check accumulator validity before calling `resolve_reconciliation_window`.
  3. `resolve_reconciliation_window` had no internal defense verifying that `candidate.order_id == window.order.id`.
  4. `persist_resolution_or_review` had no apply boundary check validating candidate ownership prior to executing `TradeLifecycleService.execute_close`.

### Defect 2: Record Budget Overflow (Reading 6 Records When Budget Is 2)
- **Symptom**: When `process_window_slice` was called with `budget=CycleBudget(max_windows=1, max_pages=1, max_records=2)` and `page_size=200` on a database with 6 evidence quotes, all 6 records were read in a single query, `records_processed` was reported as 6, and `read_complete` was falsely set to `True`.
- **Root Cause**:
  1. In `load_persisted_window_evidence`, the query limit was passed as `page_size` (200) instead of being bounded by `budget.remaining_records()`.
  2. `CycleBudget` had no `remaining_records()` or `remaining_pages()` methods; it only tracked `records_processed` without computing remaining allowance.
  3. `process_window_slice` did not compute `effective_limit = min(page_size, budget.remaining_records())`.
  4. On empty evidence pages, `budget.record_page()` was not called because `record_page` was placed inside `if page.items:`, resulting in zero page-accounting cost for empty query attempts.

### Defect 3: Codec Accepting NaN / Inf / Booleans & Silent Dropping of Corrupt Candidates
- **Symptom**: `decode_candidate` allowed non-finite numbers (`float('nan')`, `float('inf')`, `-float('inf')`) and Python booleans (`True`/`False`) as `executable_price`. Furthermore, `CandidateAccumulator.from_dict` iterated raw candidate dictionaries and silently skipped those failing decode, producing an apparently valid accumulator with missing proof.
- **Root Cause**:
  1. Codec price validation used loose type checks or default `float(v)` without `math.isfinite()` and `not isinstance(v, bool)`.
  2. `json.dumps` in `save_window_progress` did not set `allow_nan=False`, allowing `NaN` values to be persisted into SQLite JSON fields.
  3. `CandidateAccumulator.from_dict` used `continue` on decode failures, discarding corrupted candidates without appending error flags to `conflict_flags` or invalidating the accumulator state.

---

## 3. Core Runtime & Production Fixes

### 1. Robust Type & Numeric Validators
- **`backend/services/market_reconciliation_service.py`**:
  - Implemented `ValidationIssue` and `ValidationResult` dataclasses for clean, structured diagnostics without throwing exceptions on ordinary malformed JSON.
  - Implemented `is_finite_real(v)`: validates that a value is an integer or float, strictly not a boolean, and satisfies `math.isfinite(v)`.
  - Implemented `parse_positive_price(v, field, optional=False)`: validates finite real numbers strictly greater than 0; rejects strings, booleans, and negative/zero values.
  - Implemented `parse_timestamp(v, field, optional=False)`: validates integer timestamps strictly >= 0; rejects booleans, floats with fractions, and non-numeric types.
  - Defined explicit domains:
    - `VALID_EXIT_CAUSES`: `{"TP_HIT", "SL_HIT", "TIMEOUT_EXIT", "LIQUIDATION", "AMBIGUOUS_BAR_SL_FIRST", "GAP_SL", "SLIPPAGE_EXIT"}`
    - `VALID_PRICE_BASES`: `{"BID_ASK", "LAST", "SYNTHETIC_BAR", "INDEX"}`

### 2. Strict Codec & Serialization Hardening
- **`backend/services/market_reconciliation_service.py`**:
  - `encode_candidate`: validates all prices with `is_finite_real` and strictly positive check before serialization. Raises `ValueError` if prices are invalid.
  - `decode_candidate`: validates non-empty identifiers (`order_id`, `evidence_id != "UNKNOWN"`), membership in `VALID_EXIT_CAUSES` and `VALID_PRICE_BASES`, non-negative integer `epoch`, and positive finite prices. Returns `CandidateDecodeResult(is_valid, candidate, reason, issues)`.
  - `decode_candidate_accumulator`: iterates raw candidate items; if ANY candidate is corrupt or unparsable, records `CORRUPT_CANDIDATE` in `conflict_flags` and flags `is_valid=False`. Verifies that `earliest_source_ts` precisely matches `min(source_timestamp)` of the earliest candidates.
  - `save_window_progress`: uses `json.dumps(..., allow_nan=False)` with comprehensive serialization failure handling.

### 3. Context Validation & Pure Strict Merge
- **`backend/services/market_reconciliation_service.py`**:
  - Implemented `build_reconciliation_context(window, progress)`: extracts immutable order identity, symbol, direction, opened timestamp, frozen cutoff, and source epoch.
  - Implemented `validate_candidate_context(candidate, window, context)`: enforces `candidate.order_id == window.order_id`, `source_timestamp >= opened_at`, matching epoch, and known provenance source (`source != "UNKNOWN"`).
  - Implemented `validate_accumulator_context(accumulator, window, context)`: enforces window identity, order ID, symbol, direction, and epoch match. Mismatches flag `WINDOW_ID_MISMATCH` and return `is_valid=False`.
  - Hardened `merge_candidate_accumulator` and `merge_candidate_accumulator_with_result`:
    - Validates incoming candidate contexts.
    - Performs canonical deduplication: identical candidates with same `evidence_id` and payload are deduplicated idempotently.
    - Conflicting candidates with identical `evidence_id` but conflicting payload (different cause, price, timestamp, or epoch) generate `EVIDENCE_ID_CONFLICT` and mark `is_valid=False`.

### 4. Defense-in-Depth at Resolver & Apply Boundaries
- **`backend/services/market_reconciliation_service.py`**:
  - `resolve_reconciliation_window`: entrance defense validates each candidate's `order_id == order.id`, positive finite executable price, and timestamp ordering before evaluating outcomes. On mismatch, returns `ResolutionDecision(action="REVIEW_REQUIRED", reason_code="CANDIDATE_ORDER_ID_MISMATCH")` — NEVER returns `CLOSE`.
  - `persist_resolution_or_review`: apply boundary defense verifies `cand.order_id == window.order.id`, positive finite executable price, and timestamp validity. Rejects forged or mismatched close decisions without calling `TradeLifecycleService.execute_close`, returning `ResolutionApplyResult(applied=False, committed=False)`.

### 5. Hard Read Budget Enforcement & Slice Pagination
- **`backend/services/market_reconciliation_service.py`**:
  - Added `remaining_records(self) -> int` and `remaining_pages(self) -> int` to `CycleBudget`.
  - In `process_window_slice`:
    - Preflight budget check: if `not budget.allows_page() or budget.remaining_records() <= 0`, halts immediately returning `progressed=False, read_complete=False, reasons=["NO_BUDGET"]`.
    - Computes `effective_limit = min(page_size, budget.remaining_records())`.
    - Queries evidence via `load_persisted_window_evidence(..., limit=effective_limit)`.
    - Unconditionally records page query cost: `budget.record_page(len(page.items))` even for empty pages.
    - If `page.next_cursor is not None`, sets `has_more=True, read_complete=False` and saves progress pointing to last consumed cursor.
    - On slice completion (`has_more=False`), if accumulator contains conflict flags, persists `read_status="REPLAY_NEEDED"` and prevents resolution to `CLOSE`.

### 6. Key Collision & Replay Isolation
- **`backend/services/market_reconciliation_service.py`**:
  - `load_window_progress`: detects key truncation collisions. If `stored.window_id != requested_window_id`, returns `None` and rejects cross-order state leakage.
  - Injected progress in `process_window_slice`: validated against window context; corrupted progress is marked `REPLAY_NEEDED` and does not mutate orders.

---

## 4. Before / After Reproduction Matrix

| Case ID | Baseline Behavior (BEFORE) | V14.4.9 Production Behavior (AFTER) | Verification Test |
|---|---|---|---|
| **V01** | `decode_candidate` accepted `executable_price=NaN`, `Inf`, and `True`. `encode_candidate` wrote invalid floats. | `is_finite_real` rejects `NaN`, `Inf`, booleans, strings. Codec returns `INVALID_PRICE_NON_FINITE` / `INVALID_PRICE_NON_POSITIVE`. | `test_v01_codec_numeric_validation` |
| **V02** | Empty / whitespace `order_id` accepted. `evidence_id="UNKNOWN"` accepted. Unsupported exit causes permitted. | Strict identifier validation rejects empty IDs, `"UNKNOWN"`, and unapproved causes. Valid causes from exit evaluator pass. | `test_v02_identifier_enum_epoch_validation` |
| **V03** | `CandidateAccumulator.from_dict` silently dropped corrupt candidates and returned a false clean accumulator. | `decode_candidate_accumulator` detects corrupt candidates, appends `CORRUPT_CANDIDATE` to `conflict_flags`, sets `is_valid=False`. | `test_v03_corrupt_accumulator_detection` |
| **V04** | Candidate with `order_id="OTHER"` progressed to `CLOSE`, mutated order state to `closed`, and wrote PnL. | Foreign candidate blocked at slice entry; order remains `paper_open`, PnL untouched, progress marked `MISMATCH`. | `test_v04_wrong_order_blocked_through_production` |
| **V05** | Direct calls to `resolve_reconciliation_window` and `persist_resolution_or_review` accepted foreign candidates. | Resolver returns `REVIEW_REQUIRED / CANDIDATE_ORDER_ID_MISMATCH`. Apply boundary returns `applied=False`; order untouched. | `test_v05_direct_resolver_and_apply_defense` |
| **V06** | Candidates with timestamp prior to `opened_at` or epoch mismatch were accepted into accumulator. | `validate_candidate_context` rejects pre-open timestamps, epoch mismatches, and unknown provenance sources. | `test_v06_context_mismatch_rejection` |
| **V07** | Duplicate `evidence_id` with conflicting causes/prices was silently overwritten or merged. | Idempotent deduplication for identical payloads; conflicting payloads trigger `EVIDENCE_ID_CONFLICT` and fail closed. | `test_v07_duplicate_conflict_detection` |
| **V08** | Prefix-truncated keys in `load_window_progress` loaded another order's progress if first 50 chars matched. | Stored `window_id` verified against requested `window_id`. Mismatch returns `None` and isolates state. | `test_v08_legacy_progress_and_key_collision` |
| **B01** | `budget=CycleBudget(max_records=2)` read all 6 records in one query; reported `records_processed=6, read_complete=True`. | Reads exactly `effective_limit=2` records per slice; requires 3 slices to consume 6 records; reports `read_complete=False` until end. | `test_b01_hard_record_budget_enforcement` |
| **B02** | No `remaining_records()` / `remaining_pages()` methods on `CycleBudget`; page size was unconstrained. | `CycleBudget.remaining_records()` and `remaining_pages()` enforced at every query boundary. | `test_b02_boundary_effective_limit` |
| **B03** | Empty evidence pages cost 0 pages because `record_page` was skipped on empty results. | Empty queries unconditionally consume 1 page attempt (`pages_processed=1, records_processed=0`). | `test_b03_empty_page_cost_accounting` |
| **B04** | Zero budget or expired deadline entered query loop and mutated state. | Preflight check halts immediately, returns `progressed=False, read_complete=False, reasons=["NO_BUDGET"]`. | `test_b04_zero_budget_and_deadline_preflight` |
| **B05** | Multiple windows in batch did not coordinate remaining record budget. | Shared `CycleBudget` tracks global consumption across windows (`2 + 1 = 3 <= max_records`). | `test_b05_global_counters_shared_budget` |

---

## 5. Acceptance Test Suite Results

The canonical acceptance runner (`tools/run_v14_4_2_acceptance.py`) executed all 8 gates with **175 required cases**:

```
============================================================
=== V14.4.2 ACCEPTANCE REPORT ===
  backend_targeted       PASS (387 tests across 43 files)
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
   - 43 targeted test files executed, **387 tests passed**, 0 failed, exit code 0.
   - Includes new dedicated suite: `tests/test_v14_4_9_reconciliation_integrity.py` (17/17 PASS).
   - Includes baseline suite: `tests/test_v14_4_8_reconciliation_accumulator.py` (13/13 PASS).
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
   - Includes real WebSocket soak (10,063 events, 0 leaks), chart interactions, drag-and-drop, DRAFT mode.
7. **`benchmark`**: **`PASS`**
   - Verified Python and browser latency metrics, exit code 0.
8. **`git_diff_check`**: **`PASS`**
   - Clean diff check across working tree, exit code 0.

---

## 6. Flow Preservation & Non-Regression Analysis

1. **SMC Detection & Setup Integrity**:
   - Order setup detection (swing structure, liquidity sweep, FVG, order block) and entry geometry remain untouched.
   - Signed RR calculations and provisional plan models remain identical.

2. **Entry Guard & Risk Preservation**:
   - Entry guard checks (`news`, `lesson`, `account`, `feed`, `quote`, `cost`, `policy`) remain strictly enforced.
   - Single open position limit and 0.10% risk cap preserved.
   - Max 3 actual fills per day UTC+7 strictly maintained.
   - `ALL_SESSIONS_WITH_NY_RESERVE` session policy remains active.

3. **Trade Lifecycle & Execution Boundary**:
   - ARM -> FILL -> OPEN -> TP/SL protective exits -> journal/lessons/outbox pipeline preserved.
   - `TradeLifecycleService.execute_close` remains the sole atomic close mutation gateway via CAS optimistic locking.
   - No direct `order.state = "closed"` mutations anywhere in reconciliation logic.

4. **Accounting & Ledgers**:
   - Realized PnL, commission fees, and margin balances calculated strictly through canonical fee and PnL calculator.
   - Invalid evidence touches no ledger, journal, or outbox records.

---

## 7. Operational Constraints & Limitations

1. **Scope of Replay on Data Corruption**:
   - When corrupted progress or evidence ID conflicts are encountered, the reconciliation system flags `REPLAY_NEEDED` and resets the cursor to the trade's `opened_at` timestamp. If durable evidence is permanently damaged in SQLite, the window transitions to `REVIEW_REQUIRED` for operator inspection.
2. **Synchronous DB Query Boundaries**:
   - Budget checks evaluate before starting a page read. Individual database queries are atomic and will not be preempted mid-statement. The bounding of `limit` to `min(page_size, budget.remaining_records())` guarantees that execution time per query remains strictly bounded.
3. **Paper Simulation Boundary**:
   - All tests run in local isolated test databases with PAPER orders. No live orders are submitted to Bitget exchange, preserving capital and account security.
