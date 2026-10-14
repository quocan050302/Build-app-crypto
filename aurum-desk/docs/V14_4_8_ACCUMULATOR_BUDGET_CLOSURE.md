# V14.4.8 RECONCILIATION ACCUMULATOR & BUDGET FAIRNESS CLOSURE REPORT

## 1. Executive Summary & Verification Context

- **Overall Status**: **`PASS`** (All 162/162 required cases green, 8/8 gates passing, exit code 0)
- **Repository**: `quocan050302/Build-app-crypto`
- **Branch**: `fix/v14-1-rr-flow`
- **Base / HEAD Commit**: `ad2501d7dd34f08f6de4bc3db730f40c4dfbf08a` (*docs: record final implementation SHA in V14.4.7 closure report*)
- **Code Runtime Baseline**: `1a2214488695aa642c52d70ebe6ae24822d73747` (*fix(v14.4.7): fail closed on missing proof snapshots and resume reconciliation scans*)
- **Acceptance Run ID**: `run_1791940701980`
- **Tree Fingerprint**: `cf3c078832c6abfd8cfba8dbe2a9e70092a8f25f`
- **Date**: 2026-10-14
- **Preservation Assurance**: Full preservation of SMC detector, entry guards (news/lesson/account/feed/quote/cost/policy), order execution flow (ARM -> FILL -> OPEN -> TP/SL protective exits -> journal/lessons/outbox), `ALL_SESSIONS_WITH_NY_RESERVE` policy, 3 fills/day UTC+7 cap, single open position invariant, 0.10% risk cap, and immutable chart/accounting contracts.

---

## 2. Root Cause Analysis of Addressed Defects

### Defect A: Candidate Reset & Loss-First Bias Across Slices/Restarts
- **Symptom**: When a trade window's evidence spanned multiple slices (e.g. `page_size=2`, `max_pages=1`), an early Take-Profit at source time 2000 was discovered in Slice 1. In Slice 2, subsequent records arrived containing a late Stop-Loss at source time 4000. The reconciliation resolved the window as a Stop-Loss hit at 4000 (`CLOSE / SL_HIT`) instead of Take-Profit at 2000 (`CLOSE / TP_HIT`).
- **Root Cause**:
  1. `process_window_slice` initialized `all_evidence = []` locally on every invocation. Evidence from prior slices was never reloaded or combined.
  2. In `save_window_progress`, `candidate_refs` were blindly assigned from `progress.candidate_refs`, which had been initialized to `[]` and never populated with candidate metadata or merged across invocations.
  3. Resolver `resolve_reconciliation_window` was called with `all_evidence` consisting strictly of the current slice's records. As a consequence, Slice 2 had zero visibility into Slice 1's early Take-Profit, leading to an incorrect terminal decision.

### Defect B: Zero/Exhausted Page Budget False Completion & Coordinator Starvation
- **Symptom**: With 51 historical CLOSED orders requiring audit and a global cycle budget of `max_pages=20`, only 21 orders were audited across 4 cycles; 30 orders (orders 22–51) were completely bypassed. Additionally, windows with 0 remaining page budget were incorrectly marked as `COMPLETE`.
- **Root Cause**:
  1. `CycleBudget.allows_window()` checked `windows_processed < max_windows` (budget 50) without verifying `allows_page()`. When `max_pages=20` was exhausted by earlier windows, `process_window_slice` was still called with 0 page budget.
  2. Inside `process_window_slice`, the `while budget.allows_page()` loop executed 0 times. `has_more` remained initialized to `False`. The function fell through and marked the completely unread window as `COMPLETE` (`read_complete=True`), falsely certifying traversal.
  3. In `MarketReconciliationService.process_late_batch`, the coordinator unconditionally executed `save_scan_progress(db, ..., next_cursor=window_page.next_cursor)` at the end of the outer window loop. Because `window_page` loaded 50 items up front, advancing `next_cursor` to the 50-item page boundary skipped items 22–50 that were never actually processed.

---

## 3. Core Runtime & Production Fixes

### 1. Durable `CandidateAccumulator` & Strict Codec
- **`backend/services/market_reconciliation_service.py`**:
  - Implemented typed structures: `CandidateDecodeResult` and `CandidateAccumulator`.
  - Implemented `encode_candidate(candidate) -> dict` and `decode_candidate(data) -> CandidateDecodeResult`:
    - Strict validation of all `TerminalCandidate` fields: `order_id`, `evidence_id`, `source_timestamp`, `known_at_ms`, `bid`, `ask`, `last`, `price_basis`, `cause`, `executable_price`, `source_epoch`, `source`.
    - Explicit rejection of `NaN`, `Inf`, booleans for numeric fields, and missing identifier strings.
  - Designed `CandidateAccumulator` schema (version 1):
    - Stores `schema_version`, `window_id`, `query_generation`, `arrival_revision`, `earliest_source_ts`, `earliest_candidates` (encoded JSON), `conflict_flags`, `processed_interval`, and `validation_context`.
    - Fully serializable to/from JSON dict, embedded within `EvidenceProgress.accumulator`.

### 2. Pure Deterministic `merge_candidate_accumulator`
- **`backend/services/market_reconciliation_service.py`**:
  - Implemented `merge_candidate_accumulator(previous, new_candidates, context) -> CandidateAccumulator` and `merge_candidate_accumulator_with_result(...)`:
    - Validates context compatibility (order ID, symbol, direction, source epoch).
    - Deduplicates candidates by unique `evidence_id`.
    - Computes the minimum `source_timestamp` across all accumulated and incoming candidates.
    - Preserves the **earliest terminal group**: all candidates sharing the exact earliest source timestamp are retained.
    - Preserves **tie ambiguity**: if both TP and SL occur at the exact same timestamp, both candidates are retained and `conflict_flags` records `"AMBIGUOUS_SAME_TS"`.
    - Discards later candidates (e.g. later SL at 4000 when early TP at 2000 exists).
    - Idempotent across re-runs, page sizes, and service restarts.

### 3. Preflight Zero-Budget Check & Slice Accumulation
- **`backend/services/market_reconciliation_service.py`**:
  - In `process_window_slice`:
    - **Preflight check**: If `not budget.allows_page()`, immediately returns:
      `WindowSliceResult(progressed=False, has_more=True, read_complete=False, decision=None, reasons=["NO_BUDGET"])`.
      Does not advance cursor, does not call resolver, does not mark complete.
    - Loads persisted `accumulator` from `EvidenceProgress`.
    - Iterates pages bounded by `budget.allows_page()`.
    - For each page, collects `new_candidates = collect_terminal_candidates(window.order, page.items)`.
    - Merges into `accumulator = merge_candidate_accumulator(accumulator, new_candidates, context)`.
    - Commits `progress.accumulator` atomically with `progress.source_cursor` via `save_window_progress`.
    - Propagates `PROGRESS_SAVE_FAILED` if persistence fails, preventing cursor advancement.
    - Resolves terminal state strictly using `accumulator.earliest_candidates` upon true read completion (`has_more=False`), regardless of whether the final page was empty or non-terminal.

### 4. Item-Level Scheduler Checkpoint & Fair Traversal
- **`backend/services/market_reconciliation_service.py`**:
  - Extended `CycleBudget` with `can_start_window_read()`: requires both `windows_processed < max_windows` and `allows_page()`.
  - In `MarketReconciliationService.process_late_batch`:
    - Before processing each window item, checks `budget.can_start_window_read()`. If exhausted, stops immediately and checkpoints `last_fully_handled_cursor`.
    - If `slice_result` returns `read_complete=False` (e.g. `NO_BUDGET` or `MORE_WORK`), stops immediately and records `checkpoint.resume_window_id = window.order.id`.
    - Maintains `last_fully_handled_cursor = _window_cursor_for_item(window)` updated ONLY after a window is fully read and processed.
    - Advances scan progress to `window_page.next_cursor` **ONLY IF** all items in `window_page` were fully consumed.
    - If stopped mid-page, checkpoints `last_fully_handled_cursor`, ensuring the next cycle resumes from the exact next unread window without skipping.
    - Guaranteed audit of all 51 historical CLOSED orders across bounded cycles.

---

## 4. Before / After Reproduction Matrix

| Case ID | Defect / Baseline Behavior (BEFORE) | V14.4.8 Production Behavior (AFTER) | Verification Suite |
|---|---|---|---|
| **ACC01** | LONG 4180/4170/4200: Slice 1 has TP at 2000; Slice 2 has SL at 4000. Slice 2 resolved as `CLOSE / SL_HIT` at 4000. | `CandidateAccumulator` carries TP 2000 across slices. Slice 2 resolves as `CLOSE / TP_HIT` at 2000. | `tests/test_v14_4_8_reconciliation_accumulator.py::test_case_acc01_long_early_tp_before_later_sl` |
| **ACC02** | SHORT 4180/4190/4160: Slice 1 has TP at 2000; Slice 2 has SL at 4000. Resolved as `CLOSE / SL_HIT` at 4000. | Early Ask 4159 accumulated. Resolves correctly as `CLOSE / TP_HIT` at 2000. Single ledger/journal entry. | `tests/test_v14_4_8_reconciliation_accumulator.py::test_case_acc02_short_mirror_early_tp_before_later_sl` |
| **ACC03** | TP and SL occur at exact same timestamp across split pages. System arbitrarily picked one candidate or dropped ties. | Earliest group retains both TP and SL. Resolves with `confidence="AMBIGUOUS"` and `conflict_flags=["AMBIGUOUS_SAME_TS"]`. | `tests/test_v14_4_8_reconciliation_accumulator.py::test_case_acc03_tie_ambiguity_split_pages` |
| **ACC04** | Different chunk sizes (`page_size` 1, 2, 3) produced divergent resolution decisions due to unaccumulated slices. | Deterministic chunking equivalence: identical TP 2000 outcome across all page sizes and restart boundaries. | `tests/test_v14_4_8_reconciliation_accumulator.py::test_case_acc04_chunking_equivalence` |
| **ACC05** | Candidates from different order IDs or corrupted schemas were accepted or caused unhandled exceptions. | Schema validation and context check strictly reject mismatched orders and corrupted codecs; triggers safe replay. | `tests/test_v14_4_8_reconciliation_accumulator.py::test_case_acc05_corrupt_context_and_mismatch` |
| **ACC06** | Slice 1 has early TP; Slice 2 has trailing neutral/empty pages. Resolver returned `NONE` because Slice 2 was empty. | Trailing empty/neutral pages merge cleanly into accumulator. Earliest TP candidate preserved and resolved. | `tests/test_v14_4_8_reconciliation_accumulator.py::test_case_acc06_latest_page_neutral_or_empty` |
| **BUDGET01**| Page budget 0 called `process_window_slice`, loop ran 0 times, unread window falsely marked `COMPLETE`. | Preflight check returns `progressed=False, has_more=True, read_complete=False, reasons=["NO_BUDGET"]`. Cursor unchanged. | `tests/test_v14_4_8_reconciliation_accumulator.py::test_case_budget01_zero_dimensions_no_work` |
| **SCHED01**| 51 CLOSED orders with `max_pages=20`. Only 21/51 audited; 30 orders (22–51) bypassed by premature `next_cursor` advance. | Coordinator checkpoints `last_fully_handled_cursor`. Across bounded cycles, all 51/51 unique orders are audited. | `tests/test_v14_4_8_reconciliation_accumulator.py::test_case_sched01_51_unique_audits` |
| **SCHED02**| Large unfinished window in multi-window page caused coordinator to skip trailing windows upon budget exhaustion. | Coordinator checkpoints `resume_window_id`. Next cycle resumes unfinished window, completes it, and processes tail. | `tests/test_v14_4_8_reconciliation_accumulator.py::test_case_sched02_restart_unfinished_window` |
| **SCHED03**| Single active position starved or monopolized coordinator; closed order history was never reached. | Active OPEN position audited with priority; CLOSED history progresses with fair bounded quantum. Single open invariant kept. | `tests/test_v14_4_8_reconciliation_accumulator.py::test_case_sched03_fairness_open_and_closed` |
| **DURABLE01**| Crash between accumulator merge and cursor commit caused inconsistent state and lost Take-Profit events. | Atomic progress commit via `save_window_progress`. Simulated failure rolls back; retry resumes cleanly. | `tests/test_v14_4_8_reconciliation_accumulator.py::test_case_durable01_commit_failure_atomicity` |
| **REPLAY01**| Older evidence arriving with timestamp earlier than cursor was ignored, locking in premature SL. | `invalidate_affected_window_progress` detects `source_ts <= scan_cutoff`, resets cursor & accumulator for clean replay. | `tests/test_v14_4_8_reconciliation_accumulator.py::test_case_replay01_earlier_evidence_replay` |
| **CLOSE01**| Race condition between live price protective exit and reconciliation finalizer created duplicate close orders. | CAS optimistic concurrency in `execute_close` guarantees exactly one terminal transition; loser returns truthful error. | `tests/test_v14_4_8_reconciliation_accumulator.py::test_case_close01_race_monitor_and_recovery` |

---

## 5. Acceptance Test Suite Results

The canonical acceptance runner (`tools/run_v14_4_2_acceptance.py`) executed all 8 gates with 162 total required cases:

```
============================================================
=== V14.4.2 ACCEPTANCE REPORT ===
  backend_targeted       PASS (370 tests)
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
   - 42 targeted test files executed, **370 tests passed**, 0 failed, exit code 0.
   - Includes new dedicated suite: `tests/test_v14_4_8_reconciliation_accumulator.py` (13/13 PASS).
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
   - Chart render latency: p95 = **20.288ms** over 600 cycles (budget <= 50ms).
   - Realtime soak: **10,063 frames / 60s**, browser p95 commit proxy: **31.1ms** (budget <= 250ms).
8. **`git_diff_check`**: **`PASS`**
   - Zero trailing whitespace or merge conflict markers, exit code 0.

---

## 6. Flow & Policy Preservation Matrix

| Dimension | Policy / Invariant | Status | Verification Detail |
|---|---|---|---|
| **Trading Policy** | `ALL_SESSIONS_WITH_NY_RESERVE` | **PRESERVED** | Policy intact; Asia/Europe trade valid setups; NY reserve slot maintained. |
| **Daily Fills** | Max 3 actual fills / day UTC+7 | **PRESERVED** | Quota tracking and daily caps strictly enforced. ARM does not consume quota. |
| **Position Limit** | Exactly 1 open position | **PRESERVED** | Single active position concurrency invariant preserved across all lifecycles. |
| **Risk Cap** | Max 0.10% risk per trade | **PRESERVED** | Sizing formulas, leverage, margin, and cost models unchanged. |
| **SMC Detector** | Detection -> Setup -> ARM -> FILL -> Exits | **PRESERVED** | Canonical geometry, R-multiples, entry guards, and protective exits intact. |
| **Protective Exits** | Independent Realtime Exit Evaluation | **PRESERVED** | Live TP/SL exit monitoring runs independently; never blocked by reconciliation. |
| **Chart UI** | Selected Chart Header / Drawer / DRAFT mode | **PRESERVED** | Read-only order view, DRAFT drag mode with PointerCancel safety, canonical plans. |
| **Accounting & Audit** | Single Ledger / Journal / Telegram Outbox | **PRESERVED** | Single ledger entry, CAS close, idempotent outbox notifications, notes preserved. |

---

## 7. Machine Artifacts & Run Identity

- Acceptance Report File: `logs/acceptance/v14_4_2/acceptance_v14_4_2.json`
- Backend JUnit XML: `logs/acceptance/v14_4_2/backend_junit.xml`
- Vitest JUnit XML: `logs/acceptance/v14_4_2/vitest_junit.xml`
- Browser Playwright Report: `logs/acceptance/v14_4_2/playwright_report.json`
- Benchmark Output: `logs/acceptance/v14_4_2/benchmark.json`
- Browser Benchmark: `logs/acceptance/v14_4_2/browser_benchmark.json`
