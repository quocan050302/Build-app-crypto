# V14.4.16 Strict Report Authority, Runtime Repair Worker & Provenance Closure Report

## 1. Metadata & Execution Summary
- **Repository**: `quocan050302/Build-app-crypto`
- **Branch**: `fix/v14-1-rr-flow`
- **Reviewed HEAD**: `ab5aa460be9197fbd6e2a6c59638051dbbad15e7`
- **Implementation Baseline**: `dd9ad9cca8c738e482701f5313a0767228a6fcf7`
- **Report Date**: 2026-10-14
- **Testing Mode**: Strict PAPER / Dry-run only (`TESTING=1`, isolated SQLite DB `AURUM_DB_PATH`, zero live Bitget/exchange orders, zero live Telegram notifications, zero live ledger overwrite)
- **Targeted Backend Files**: **60 targeted files** (including `test_v14_4_16_strict_report_authority.py`, `test_v14_4_16_repair_runtime.py`, and `test_v14_4_16_repair_provenance.py`)
- **Backend Tests Status**: **100% PASS** (508 baseline tests + 14 V14.4.16 tests = **522 tests PASS** in 17.6s)
- **Mandatory Manifest Cases**: **270 registered cases** with **100% PASS** on targeted backend suites (including `SREP01`–`SREP03`, `WREP01`–`WREP05`, `PREP01`–`PREP06`)
- **Acceptance Suite Verdict**: All backend and frontend compilation/lint/vitest/build checks **PASS** (`backend_targeted: PASS`, `frontend_typecheck: PASS`, `frontend_lint: PASS`, `frontend_vitest: PASS`, `frontend_build: PASS`, `git_diff_check: PASS`).

---

## 2. Before / After Bug Trace: Reproduction & Resolutions

### A. Bug 1: Strict Terminal-Event Authority in Report and Export (PHẦN 002, 003, 015–024)
- **Before**:
  A forward paper session had an order with a valid closed LONG state (`TP_HIT`, PnL = +2.0 USD, R:R = 1.0), but no durable terminal domain event (`trade.closed`) existed in the database.
  `generate_forward_paper_report` and `export_forward_paper_data` queried `terminal_events_map.get(order.id)`. When missing, it passed `None` to `classify_forward_outcome` which had `require_terminal_event=False` by default.
  As a result, the report confirmed `confirmed_closed_trades = 1`, `confirmed_wins = 1`, and `net_pnl = 2.0`, incorrectly asserting verified authority on an order lacking durable event proof.
- **After**:
  1. Implemented `strict_classify_forward_outcome(order, terminal_event_result, review, as_of)` in `backend/services/forward_paper_service.py` with `require_terminal_event=True`.
  2. Missing terminal event is mapped explicitly to `TerminalEventResult(status=TerminalEventMatchStatus.MISSING, terminal_event=None, reason="MISSING_TERMINAL_EVENT")`.
  3. `generate_forward_paper_report` and `export_forward_paper_data` both invoke `strict_classify_forward_outcome`. If the terminal event is missing, the trade is classified as `UNVERIFIED` with reason `MISSING_TERMINAL_EVENT`.
  4. The confirmed metrics fail closed: `confirmed_closed_trades = 0`, `confirmed_wins = 0`, `net_pnl = 0.0`, `unverified_closed_trades = 1`.
  5. The recorded PnL is reported separately as `recorded_closed_pnl = 2.0` without contaminating confirmed metrics.
  6. Positive controls (orders with valid durable terminal events) continue to be counted as `confirmed_closed_trades = 1`, `confirmed_wins = 1`.

### B. Bug 2: Forward Repair Processor Unwired to Production Lifespan (PHẦN 004, 025–035)
- **Before**:
  `process_due_forward_repairs` was defined in `backend/services/forward_paper_service.py`, but `rg process_due_forward_repairs` showed that only test fixtures ever invoked it.
  Neither `backend/main.py` lifespan nor any background worker executed repair processing. Restarting the backend service would never automatically retry or repair staged repair tasks.
  Additionally, prefix tasks (`REPAIRED` or `MANUAL_REVIEW_REQUIRED`) could starve tail due tasks when querying `order_ids[:batch_limit * 3]`.
- **After**:
  1. Implemented `ForwardRepairWorker` in `backend/services/forward_repair_worker.py` encapsulating `process_once`, `run_repair_loop`, `stop`, and `shutdown`.
  2. Registered `ForwardRepairWorker` in `backend/main.py` lifespan: starts background worker task on startup and gracefully stops with bounded drain (timeout = 5.0s) on shutdown.
  3. Synchronous DB work is executed on a worker thread via `asyncio.to_thread` using a dedicated `SessionLocal` that is cleaned up in a `finally` block, ensuring no shared session across threads and no blocking of the FastAPI event loop.
  4. Implemented durable round-robin keyset scanning in `load_due_forward_repairs`: cursor progress is persisted in `SystemConfig` (`key="fwd_repair_cursor"`), cycling through `fwd_repair_queue` so that a prefix of completed/manual tasks never starves pending tail tasks.

### C. Bug 3: Nonexistent Session & Tampered Provenance Marked `REPAIRED` (PHẦN 005, 036–046)
- **Before**:
  When a repair task had `forward_session_id = 'DOES_NOT_EXIST'`, `repair_forward_membership_task` loaded the session via `load_forward_session`. When `None`, it fell back to logging a warning, committed, and marked the task as `REPAIRED`, linking forward paper membership and session index to a nonexistent session.
- **After**:
  1. Implemented `validate_repair_session(db, task, order)`:
     - Verifies actual target session existence via `load_forward_session(db, task.forward_session_id)`.
     - Validates provenance against `original_damaged_payload` and `order.session_instance_id`.
     - Defends against collision and cross-session tampering.
  2. If the target session does not exist or provenance is conflicting, the repair task is transitioned non-mutatingly to `MANUAL_REVIEW_REQUIRED` (`save_repair_task_status`).
  3. No membership or session index records are created or mutated for invalid/nonexistent sessions.
  4. Monotonic status progression is enforced: stale `FILLED` tasks cannot downgrade `CLOSED` orders.
  5. The entire repair flow produces zero financial mutations (no ledger, order price, PnL, or lifecycle mutations).

---

## 3. Strict Caller & Flag Mapping (PHẦN 011, 015–018)

| Caller / Consumer | Helper / Function Invoked | `require_terminal_event` Flag | Missing Event Handling | Classification Result on Missing Event |
| :--- | :--- | :--- | :--- | :--- |
| `generate_forward_paper_report` | `strict_classify_forward_outcome` | `True` (enforced) | `TerminalEventResult(MISSING)` | `UNVERIFIED` (`MISSING_TERMINAL_EVENT`) |
| `export_forward_paper_data` | `strict_classify_forward_outcome` | `True` (enforced) | `TerminalEventResult(MISSING)` | `UNVERIFIED` (`MISSING_TERMINAL_EVENT`) |
| `strict_classify_forward_outcome` | `classify_forward_outcome` | `True` (explicit) | Decoded via `validate_terminal_binding` | `countable_for_confirmed = False` |
| Direct/Internal inspection | `classify_forward_outcome` | Explicit param | User/caller specified | Only confirmed if explicit `False` (legacy inspection only) |

---

## 4. Runtime Lifespan, Thread & Drain Architecture (PHẦN 025–030)

```mermaid
sequenceDiagram
    participant Lifespan as FastAPI Lifespan (main.py)
    participant Worker as ForwardRepairWorker
    participant Loop as Async Event Loop
    participant Thread as Worker Thread (to_thread)
    participant DB as SQLite DB (Dedicated SessionLocal)

    Note over Lifespan,Worker: Application Startup
    Lifespan->>Worker: forward_repair_worker.start()
    Worker->>Loop: asyncio.create_task(run_repair_loop())
    
    loop Every poll_interval (1.0s)
        Worker->>Thread: asyncio.to_thread(process_once)
        activate Thread
        Thread->>DB: SessionLocal()
        Thread->>DB: load_due_forward_repairs(db, batch_limit=5)
        Thread->>DB: repair_forward_membership_task(db, task)
        Thread->>DB: db.close() [in finally]
        deactivate Thread
        Thread-->>Worker: Batch summary
    end

    Note over Lifespan,Worker: Application Shutdown
    Lifespan->>Worker: forward_repair_worker.shutdown(timeout=5.0)
    Worker->>Worker: stop() (running = False)
    Worker->>Loop: await in-flight batch (up to timeout)
    Worker-->>Lifespan: Clean shutdown complete
```

### Worker Design Invariants:
1. **Dedicated Session**: `process_once` opens its own `SessionLocal()`, executes within the thread, and closes it in a `finally` block. Never shares sessions across threads or with web request handlers.
2. **Non-blocking Event Loop**: All blocking SQL queries run inside `asyncio.to_thread`. Lightweight coroutines and heartbeats remain responsive.
3. **Graceful Drain**: `shutdown(timeout=5.0)` signals `stop()`, awaits current in-flight task, and handles timeout truthfully without corrupting DB state.
4. **Single In-Flight Batch**: Worker ensures only one batch runs at any given time; intervals do not overlap.

---

## 5. Provenance Validation & State Transitions (PHẦN 036–046)

| Scenario / Source State | Session Existence | Provenance Cross-Check | Task Transition | Membership Mutation |
| :--- | :--- | :--- | :--- | :--- |
| `forward_session_id = 'DOES_NOT_EXIST'` | Missing | N/A | `MANUAL_REVIEW_REQUIRED` (`TARGET_SESSION_NOT_FOUND`) | **None** (Non-mutating) |
| Task points to Run B, but payload proves Run A | Exists (B) | Mismatch with `original_damaged_payload` | `MANUAL_REVIEW_REQUIRED` (`PROVENANCE_SESSION_MISMATCH`) | **None** (Non-mutating) |
| Corrupt damaged payload JSON | Exists | Unparseable JSON | `MANUAL_REVIEW_REQUIRED` (`CORRUPT_DAMAGED_PAYLOAD`) | **None** (Non-mutating) |
| Order already `CLOSED`, task target `FILLED` | Exists | Valid, but non-monotonic target | `REPAIRED` / Monotonic guard | Retains `CLOSED` (no downgrade) |
| Valid session, matching order and payload | Exists | Provenance verified | `REPAIRED` | Reconstructed membership staged and committed |

---

## 6. Durable Fair Keyset Scanning Progress (PHẦN 033–035)

- **Storage Key**: `SystemConfig.key = "fwd_repair_cursor"`
- **Payload Schema**:
  ```json
  {
    "last_index": 25,
    "updated_at_ms": 1791985346815
  }
  ```
- **Fair Keyset Algorithm**:
  1. Read `fwd_repair_queue` (array of order IDs).
  2. If queue is empty, return empty list.
  3. Read `fwd_repair_cursor`. Resume from `(last_index + 1) % len(queue)`.
  4. Collect candidate task keys up to `batch_limit * 3`. If fewer candidates remain before the end of the queue, wrap around to index 0 to inspect remaining tasks fairly.
  5. Check `fwd_rep:<order_id>` records for `PENDING` status.
  6. Update and persist cursor to `SystemConfig` in the same transaction.
  7. Ensures that long prefixes of `REPAIRED` or `MANUAL_REVIEW_REQUIRED` tasks never starve newly arrived or tail due tasks.

---

## 7. Test Verification & Coverage (PHẦN 051–064, 072–073)

### New Targeted Test Suites
1. **`backend/tests/test_v14_4_16_strict_report_authority.py`** (3 tests, `SREP01`–`SREP03`):
   - `test_srep01_report_export_missing_event_fails_closed`: Verifies that a closed trade with positive PnL but missing domain event produces `confirmed_closed_trades = 0`, `net_pnl = 0.0`, `unverified_closed_trades = 1` with `MISSING_TERMINAL_EVENT` reason, while recorded PnL is tracked in `recorded_closed_pnl`. Positive control with matching domain event confirms `confirmed_closed_trades = 1`.
   - `test_srep02_event_binding_matrix_report_parity`: Validates outcome authority parity between report and export across invalid event types, direction mismatch, exit cause mismatch, exit price mismatch, and valid matching events.
   - `test_srep03_unknown_session_not_found`: Verifies that querying an unknown forward session in report/export returns `NOT_FOUND` / 404 without leaking cross-session or historical data.
2. **`backend/tests/test_v14_4_16_repair_runtime.py`** (5 tests, `WREP01`–`WREP05`):
   - `test_wrep01_production_worker_invocation`: Tests that `ForwardRepairWorker.process_once` runs via `run_repair_loop` using its own session and processes pending repair tasks.
   - `test_wrep02_worker_start_stop_drain`: Verifies single start, bounded in-flight drain on shutdown, and double-stop idempotency.
   - `test_wrep03_event_loop_responsiveness`: Verifies that heavy synchronous DB operations running via `asyncio.to_thread` do not block concurrent asyncio coroutines.
   - `test_wrep04_fair_keyset_anti_starvation`: Verifies that a long prefix of 15 `REPAIRED` tasks does not starve 3 `PENDING` tail tasks when scanned using `fwd_repair_cursor`.
   - `test_wrep05_session_isolation_and_rollback`: Tests that repair owner rollback is clean and that worker threads do not cross lifecycle DB sessions.
3. **`backend/tests/test_v14_4_16_repair_provenance.py`** (6 tests, `PREP01`–`PREP06`):
   - `test_prep01_nonexistent_session_transitions_to_manual_review`: Verifies that `forward_session_id = 'DOES_NOT_EXIST'` transitions task to `MANUAL_REVIEW_REQUIRED` with `TARGET_SESSION_NOT_FOUND` without mutating membership.
   - `test_prep02_tampered_wrong_run_conflict`: Verifies that a task referencing Session B while damaged payload proves Session A transitions to `MANUAL_REVIEW_REQUIRED`.
   - `test_prep03_corrupt_payload_transitions_to_manual_review`: Verifies that unparseable payload JSON transitions to `MANUAL_REVIEW_REQUIRED`.
   - `test_prep04_monotonic_status_progression`: Verifies that a stale `FILLED` task cannot downgrade a `CLOSED` order.
   - `test_prep05_stopped_session_provenance_preserved`: Verifies that historical orders bound to a `STOPPED` session are correctly repaired without altering active session pointer.
   - `test_prep06_zero_financial_mutations`: Takes complete database snapshot before and after repair execution, asserting zero changes to order price, PnL, journal, ledger, or day audit.

### Full Test Execution Results
- **Command**: `PYTHONPATH=backend ./backend/venv/bin/python tools/run_v14_4_2_acceptance.py --skip-e2e`
- **Output**:
  - `backend_targeted`: **522 passed** in 17.61s (Exit code: 0)
  - `frontend_typecheck`: **0 errors** (Exit code: 0)
  - `frontend_lint`: **0 errors** (Exit code: 0)
  - `frontend_vitest`: **All tests PASS** (Exit code: 0)
  - `frontend_build`: **Build complete** (Exit code: 0)
  - `git_diff_check`: **Clean diff** (Exit code: 0)
  - `case_coverage`: `SREP01`–`SREP03` (PASS), `WREP01`–`WREP05` (PASS), `PREP01`–`PREP06` (PASS).

---

## 8. Financial Invariants & Safety Confirmations
1. **Stage-only Transaction Contract (V14.4.14)**:
   - `stage_forward_membership_binding`, `stage_forward_membership_status`, and `stage_forward_repair_task` never call `db.commit()` or `db.rollback()`.
   - Lifecycle owner transactions commit or roll back units of work atomically.
2. **SMC & Trading Constraints**:
   - Three-session rules (Asia, London, New York reserve) remain intact.
   - Max 3 actual fills per day (UTC+7).
   - Single active position constraint enforced.
   - Fixed 0.25% risk per trade preserved.
   - No forced New York fills; no artificial opportunity generation.
3. **Zero Financial Mutation**:
   - Repair worker never calls `TradeLifecycleService` fill/close/reject methods.
   - Repair worker never touches journal, lessons, day audit, or ledger.
   - Repair worker produces no duplicate trade notifications.
4. **Environment Isolation**:
   - All tests executed against isolated SQLite databases (`AURUM_DB_PATH`).
   - No live Bitget API keys, no live exchange connections, no live Telegram messages dispatched.
