# V14.4.14 Transaction Ownership & Outcome Authority Repair Closure Report

## 1. Metadata & Execution Summary
- **Repository**: `quocan050302/Build-app-crypto`
- **Branch**: `fix/v14-1-rr-flow`
- **Base Reviewed HEAD**: `e5d51ab5bd39fb8fa9dbfd5e32b08c56ee0fa003`
- **Implementation Baseline**: `edaf69d0a0954297f36b867b11da7c298fa0cb22`
- **Report Date**: 2026-10-14
- **Testing Mode**: Strict PAPER / Dry-run only (`TESTING=1`, temporary isolated DB, no real Bitget/exchange orders, no real Telegram messages, zero live ledger overwrite)
- **Targeted Backend Files**: **55 targeted files** (including `test_v14_4_14_membership_transaction.py` and `test_v14_4_14_outcome_authority.py`)
- **Backend Tests Status**: **100% PASS** (475 baseline tests + 19 V14.4.14 tests = **494 tests PASS**)
- **Mandatory Manifest Cases**: **242 / 242 PASS** (including TXN01–TXN05 and AUTH03–AUTH07)
- **Acceptance Suite Verdict**: **OVERALL PASS** (Backend, Frontend Typecheck, Oxlint, Vitest, Frontend Build, Playwright Browser E2E, Benchmark, and Git Diff Check all PASSED)

---

## 2. Before / After Bug Trace: Reproduction at CLOSE (PHẦN 002, 047)

### A. The Defect (Before Fix)
In baseline V14.4.13, `update_order_forward_membership_status` (called by `TradeLifecycleService.execute_close`, `execute_fill`, and `execute_reject`) directly performed:
```python
# Legacy helper in forward_paper_service.py:
def update_order_forward_membership_status(db, order_id, new_status):
    try:
        # ... mutated membership ...
        db.commit()   # <-- PREMATURE NESTED COMMIT
        return True
    except Exception as e:
        db.rollback() # <-- UNWANTED NESTED ROLLBACK OF PARENT TRANSACTION!
        return False
```
When reviewer injected a `db.commit()` failure inside `update_order_forward_membership_status`:
1. The helper caught the error, called `db.rollback()` which **silently cancelled the parent transaction** (rolling back `order.state = "closed"`, `actual_exit = 4200.0`, `realized_pnl_net`, `DayAudit`, and `trade.closed` domain events).
2. The helper returned `False`.
3. `TradeLifecycleService.execute_close` ignored the `False` return code and attempted its own final commit: `db.commit()`.
4. `execute_close` returned the in-memory `order` object which appeared closed in RAM.
5. **DB Refresh Reality**: When refreshed from DB or queried in a fresh session, the order was still `paper_open`, `actual_exit = None`, no `trade.closed` event in DB, and no closed ledger entry. This was an unhandled nested rollback wiping out trade mutations.

### B. The Resolution (After Fix)
1. **Stage-Only Helpers**:
   - Replaced committing sub-helpers with stage-only helpers:
     - `stage_forward_membership_binding(db, session_id, order, ...)`
     - `stage_forward_membership_status(db, order_id, new_status, ...)`
     - `stage_order_to_active_forward_session(db, order, ...)`
   - Stage-only helpers **NEVER** call `db.commit()` or `db.rollback()`.
   - They perform `db.flush()` within the parent transaction so staged records are visible to intra-transaction queries without committing.
   - Return structured `MembershipStageResult`:
     - `status`: `STAGED` | `ALREADY_APPLIED` | `NOT_BOUND` | `DATA_INVALID`
     - `tracking_repair_needed`: boolean flag for soft-degrade.
2. **Trading Lifecycle Transaction Owner**:
   - `TradeLifecycleService` (`execute_fill`, `execute_close`, `execute_reject`) and `StrategyService` (`_auto_arm_candidate`) are the sole owners of unit-of-work transactions.
   - Owner stages the trade mutations, events, audits, and membership changes.
   - Owner commits exactly once at the boundary.
   - If owner commit fails: Owner performs `db.rollback()` and truthfully returns `None` (or `armed=False`), with no false success.

---

## 3. Touched Helper & Transaction Owner Permission Map (PHẦN 036)

| Component / Function | Role | Owns Transaction? | `db.commit()` Permitted? | `db.rollback()` Permitted? | Exception Behavior |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `stage_forward_membership_binding` | Sub-helper (forward paper) | No (participates in caller session) | ❌ **FORBIDDEN** | ❌ **FORBIDDEN** | SQL errors propagate to owner; pure payload errors return `DATA_INVALID` |
| `stage_forward_membership_status` | Sub-helper (forward paper) | No (participates in caller session) | ❌ **FORBIDDEN** | ❌ **FORBIDDEN** | SQL errors propagate to owner; pure payload errors return `DATA_INVALID` |
| `stage_order_to_active_forward_session` | Sub-helper (forward paper) | No (participates in caller session) | ❌ **FORBIDDEN** | ❌ **FORBIDDEN** | SQL errors propagate to owner; missing session returns `NOT_BOUND` |
| `bind_order_to_forward_session` | Standalone Convenience Wrapper | Yes (standalone callers only) | ✅ Yes (isolated) | ✅ Yes (isolated) | Rollback isolated transaction; returns failure dict |
| `bind_order_to_active_forward_session` | Standalone Convenience Wrapper | Yes (standalone callers only) | ✅ Yes (isolated) | ✅ Yes (isolated) | Rollback isolated transaction; returns failure dict |
| `update_order_forward_membership_status` | Standalone Convenience Wrapper | Yes (standalone callers only) | ✅ Yes (isolated) | ✅ Yes (isolated) | Rollback isolated transaction; returns `False` |
| `TradeLifecycleService.execute_fill` | Lifecycle Boundary Owner | **YES (Owner)** | ✅ **Single boundary commit** | ✅ **Rollback on failure** | Catches SQL/commit error $\to$ rollback, logs error, returns `None` |
| `TradeLifecycleService.execute_close` | Lifecycle Boundary Owner | **YES (Owner)** | ✅ **Single boundary commit** | ✅ **Rollback on failure** | Catches SQL/commit error $\to$ rollback, logs error, returns `None` |
| `TradeLifecycleService.execute_reject` | Lifecycle Boundary Owner | **YES (Owner)** | ✅ **Single boundary commit** | ✅ **Rollback on failure** | Catches SQL/commit error $\to$ rollback, logs error, returns `None` |
| `StrategyService._auto_arm_candidate` | Lifecycle Boundary Owner | **YES (Owner)** | ✅ **Single boundary commit** | ✅ **Rollback on failure** | Catches SQL/commit error $\to$ rollback, logs error, returns `{"armed": False, ...}` |
| `MarketReconciliationService.persist_resolution_or_review` | Lifecycle Coordinator | Delegates to `execute_close` | N/A | N/A | If `execute_close` returns `None`, sets `applied=False`, `committed=False` |

---

## 4. Four-Path Failure Matrix: ARM / FILL / CLOSE / REJECT (PHẦN 047–056)

| Path | Normal Case (Positive Control) | Pure Tracking Invalid (Soft Degrade) | SQL / DB Failure at Owner (Fail Closed) | Retry / Concurrency Semantics |
| :--- | :--- | :--- | :--- | :--- |
| **ARM** (`_auto_arm_candidate`) | Setup $\to$ `ARMED`, order $\to$ `armed`, `order.armed` event, membership `ARMED` committed in 1 TX. | Payload corrupt $\to$ trade arms cleanly; membership flags repair needed; diagnostics logged. | Commit fails $\to$ whole TX rolled back; setup remains `READY`; no orphan order or event; returns `armed=False`. | Retry with same candidate identity is idempotent; no duplicate armed orders created. |
| **FILL** (`execute_fill`) | Order $\to$ `paper_open`, `actual_entry` set, quota/DayAudit updated, `trade.opened` event, membership `FILLED` committed in 1 TX. | Payload corrupt $\to$ trade fills cleanly; DayAudit & quota preserved; tracking repair flag additive. | Commit fails $\to$ whole TX rolled back; order remains `armed`; quota not deducted; returns `None`. | Single-open position CAS enforced. Repeat fill fails closed. |
| **CLOSE** (`execute_close`) | Order $\to$ `closed`, `actual_exit` & PnL/R set, DayAudit recorded, `trade.closed` event, membership `CLOSED` committed in 1 TX. | Payload corrupt $\to$ protective exit proceeds; order closes cleanly; membership repair flagged; returns `closed_order`. | Commit fails $\to$ whole TX rolled back; order remains `paper_open`; no closed event; returns `None`. | Idempotent CAS (`state == 'paper_open'`). Winner closes once; loser returns `None`. Retry closes once DB recovers. |
| **REJECT** (`execute_reject`) | Order $\to$ `rejected`, invalidation reason set, `trade.rejected` event, membership `REJECTED` committed in 1 TX. | Payload corrupt $\to$ rejection proceeds; setup invalidated cleanly; returns `rejected_order`. | Commit fails $\to$ whole TX rolled back; order remains in pre-rejection state; returns `None`. | Idempotent retry. Quota is untouched. |

---

## 5. Outcome Authority Extraction & Real ORM Field Mapping (PHẦN 004, 037–046)

### A. Real ORM Model Columns
- `models.PaperOrder.recovery_status`: String column (`"NONE"`, `"RECOVERING"`, `"RECOVERED"`, `"UNRESOLVED_GAP"`).
- `models.PaperOrder.recovery_confidence`: String column (`"CONFIRMED"`, `"CONFIRMED_TICK"`, `"SIMULATED_AMBIGUOUS"`, `"ASSUMED_CONSERVATIVE"`, `"UNRESOLVED"`).
- **Hypothetical `order.confidence` is NEVER used**.

### B. Authority Classification Matrix

| `recovery_confidence` | `recovery_status` | `exit_cause` | Review Record Status | Extracted Status (`RecoveryOutcomeAuthority`) | `classify_forward_outcome` Classification | Countable for Confirmed Denominator? |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `None` | `NONE` | `TP_HIT` / `SL_HIT` | `None` | `VERIFIED_PAPER` (`exit_mode="NORMAL"`) | `CONFIRMED` | ✅ **True** |
| `None` | `NONE` | `MANUAL` / `MANUAL_CLOSE` | `None` | `VERIFIED_PAPER` (`exit_mode="MANUAL"`) | `CONFIRMED` | ✅ **True** |
| `CONFIRMED_TICK` | `RECOVERED` | `TP_HIT_RECONCILED` / any valid | `None` | `VERIFIED_PAPER` (`exit_mode="RECOVERY"`) | `CONFIRMED` | ✅ **True** |
| `CONFIRMED` | `RECOVERED` | Valid cause | `None` | `VERIFIED_PAPER` (`exit_mode="RECOVERY"`) | `CONFIRMED` | ✅ **True** |
| `SIMULATED_AMBIGUOUS` | `RECOVERED` | Any valid | `None` | `CONSERVATIVE_SIMULATION` | `CONSERVATIVE_SIMULATION` | ❌ **False** (isolated) |
| `ASSUMED_CONSERVATIVE` | `RECOVERED` | Any valid | `None` | `CONSERVATIVE_SIMULATION` | `CONSERVATIVE_SIMULATION` | ❌ **False** (isolated) |
| Any / `None` | Any | `AMBIGUOUS_BAR_SL_FIRST` | `None` | `CONSERVATIVE_SIMULATION` | `CONSERVATIVE_SIMULATION` | ❌ **False** (isolated) |
| `UNRESOLVED` | `UNRESOLVED_GAP` / `RECOVERING` | Any | `None` | `UNRESOLVED` | `REVIEW_PENDING` | ❌ **False** |
| Any | Any | Any | `PENDING_REVIEW` / `RETRYING` | `UNRESOLVED` | `REVIEW_PENDING` | ❌ **False** |
| Any | Any | Any | `MANUAL_REVIEW_REQUIRED` | `CONFLICTED` | `CONFLICTED` | ❌ **False** |
| Any | Any | Non-standard string | `None` | `UNVERIFIED` | `UNVERIFIED` | ❌ **False** |
| Any | Any | Valid | `closed_at=None` | `UNVERIFIED` | `UNVERIFIED` | ❌ **False** |

### C. Safe Math & As-Of Invariants
- `realized_pnl_net is None` $\to$ classified as `DATA_INVALID` (`MISSING_REALIZED_PNL`), **NEVER** falsely treated as breakeven.
- `realized_pnl_net` is `NaN` or `Inf` $\to$ classified as `DATA_INVALID` (`NON_FINITE_REALIZED_PNL`).
- `realized_r is None` $\to$ preserved as `None` (valid R count incremented only when `r` is finite; missing R count tracked).
- `closed_at > as_of_ms` $\to$ classified as `OPEN` as-of the cutoff; future trades are never leaked into past historical reports.
- `build_forward_session_report` is a **pure read query** with zero DB mutations.

---

## 6. Verification Commands & Acceptance Results

### A. Dedicated V14.4.14 Test Suites
```bash
TESTING=1 AURUM_DB_PATH=/tmp/v14_4_14_both.db PYTHONPATH=. \
./venv/bin/python -m pytest tests/test_v14_4_14_membership_transaction.py tests/test_v14_4_14_outcome_authority.py -v
```
**Result**: **19 / 19 PASS** in 0.74s
- `test_txn01_red_close_repro_no_helper_rollback` (TXN01 / T01): **PASSED**
- `test_t02_normal_close_atomic`: **PASSED**
- `test_txn02_owner_commit_failure_and_retry` (TXN02 / T03): **PASSED**
- `test_txn03_corrupt_membership_pure_validation_soft_degrade` (TXN03 / T04): **PASSED**
- `test_t05_sql_error_aborts_owner_transaction`: **PASSED**
- `test_txn04_fill_failure_matrix_and_soft_degrade` (TXN04 / T06): **PASSED**
- `test_t07_arm_failure_matrix_atomic`: **PASSED**
- `test_t08_reject_failure_matrix`: **PASSED**
- `test_txn05_mid_transition_visibility_no_helper_commit` (TXN05 / T09): **PASSED**
- `test_t10_concurrency_cas_and_idempotency`: **PASSED**
- `test_auth03_real_orm_authority_fields` (AUTH03 / A01): **PASSED**
- `test_auth04_normal_and_manual_positive_control[TP_HIT-NORMAL]` (AUTH04 / A02): **PASSED**
- `test_auth04_normal_and_manual_positive_control[SL_HIT-NORMAL]`: **PASSED**
- `test_auth04_normal_and_manual_positive_control[MANUAL-MANUAL]`: **PASSED**
- `test_auth04_normal_and_manual_positive_control[MANUAL_CLOSE-MANUAL]`: **PASSED**
- `test_auth05_recovery_positive_controls[CONFIRMED_TICK]` (AUTH05 / A03): **PASSED**
- `test_auth05_recovery_positive_controls[CONFIRMED]`: **PASSED**
- `test_auth06_ambiguity_conservative_simulation_and_conflict_review` (AUTH06 / A04): **PASSED**
- `test_auth07_as_of_missing_nan_and_export_parity` (AUTH07 / A05): **PASSED**

### B. Full Canonical Acceptance Runner
```bash
./backend/venv/bin/python tools/run_v14_4_2_acceptance.py
```
**Result**: **OVERALL PASS** (Exit Code 0)
- `backend_targeted`: **PASS** (55 files, 494 tests, 18.7s)
- `frontend_typecheck`: **PASS** (`tsc -b`, 3.6s)
- `frontend_lint`: **PASS** (`oxlint`, 0 warnings, 0 errors, 0.4s)
- `frontend_vitest`: **PASS** (1.4s)
- `frontend_build`: **PASS** (`vite build`, 4.5s)
- `browser_e2e`: **PASS** (Playwright 5 suites, 163.4s)
- `benchmark`: **PASS** (600 cycles, p95 = 7.24 ms < 250 ms limit, memory growth = 0.12 MB < 25 MB limit, 4.8s)
- `git_diff_check`: **PASS** (0.0s)
- **Mandatory Cases Checked**: **242 / 242 PASS**

---

## 7. Operational Boundaries & Preserved Invariants

1. **Strategy & Policy Preservation**:
   - Zero changes to SMC indicators, Fair Value Gaps, Order Blocks, Liquidity Sweeps, or Market Structure Shifts.
   - Preserved `ALL_SESSIONS_WITH_NY_RESERVE` entry policy across Asia, London, and NY sessions.
   - Daily fill cap of 3, maximum 1 open position, and 1 armed order strictly preserved.
   - Risk percentages (0.25% standard, hard cap 0.10% for fallback) and isolated margin untouched.
2. **Protective Exit Preservation**:
   - TP/SL protective exits remain unconditionally operational outside trading windows, when Auto is OFF, or when daily fill quota is exhausted.
   - Long exits at Bid, Short exits at Ask.
3. **Accounting Integrity**:
   - Single canonical financial calculator (`domain_calculator.py`) and atomic DayAudit update.
   - Zero duplicated cashflows or synthetic PnL.
4. **No Real Orders or External Side Effects**:
   - Zero live exchange API calls.
   - Zero real Telegram dispatch.
