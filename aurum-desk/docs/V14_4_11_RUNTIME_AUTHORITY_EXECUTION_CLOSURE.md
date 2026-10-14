# V14.4.11 Runtime Authority & Three-Session Execution Closure Report

## 1. Metadata & Execution Summary
- **Repository**: `quocan050302/Build-app-crypto`
- **Branch**: `fix/v14-1-rr-flow`
- **Baseline SHA**: `041458d`
- **Review HEAD**: `26b6b13b3cf529caa2f4c2c1995a68326e60f609`
- **Runner Run ID**: `run_1791953198423`
- **Tree Fingerprint**: `4f1f19810355e1f3b76f7a03d41edd8cfbc9538c`
- **Report Timestamp**: `2026-10-14 04:51:45 UTC` (11:51:45 local)
- **Overall Verdict**: **PASS** (Exit code 0)
- **Targeted Backend Files**: 47 files (407 baseline + 23 reconciliation authority + 15 three-session execution = 445 tests PASS, 0 FAIL)
- **Mandatory Manifest Cases**: 211 / 211 **PASS** (100% coverage, 0 missing, 0 incomplete)

---

## 2. Before / After Repros: 3 Core Runtime Bugs

### Bug 1: Corrupt Frozen Snapshot Fails Closed (PHẦN 002, 013-024, 053)
- **Before Fix**:
  - Seed order with `cost_snapshot='{broken'`, entry 4180 / SL 4170 / TP 4200.
  - `read_frozen_exit_context` returned `status='INVALID', issues=['CORRUPT_COST_SNAPSHOT_JSON']`.
  - However, `build_reconciliation_context` ignored `status='INVALID'` and hashed fallback fields to synthesize a valid context dictionary.
  - As a result, `process_window_slice` with complete continuity proof issued `action='CLOSE'`, mutating order state and triggering ledger/journal accounting on invalid data.
- **After Fix**:
  - `read_frozen_exit_context` enforces strict JSON parsing, root dictionary validation (`ROOT_NOT_DICT`), and valid geometric ordering (`LONG_SL_GTE_ENTRY`, `SHORT_SL_LTE_ENTRY`).
  - `build_reconciliation_context_result` fails closed immediately if `frozen.status == 'INVALID'`, returning `ContextBuildResult(status='INVALID', context=None, issues=frozen.issues)`.
  - `process_window_slice` halts without resolving or closing the window: `decision=None`, `read_complete=False`, `reasons=['CORRUPT_COST_SNAPSHOT_JSON']`.
  - Direct apply boundary defense in `persist_resolution_or_review` and `MarketReconciliationService.apply_reconciliation_decision` checks `read_frozen_exit_context(order)`: forged or direct CLOSE calls are rejected with `applied=False, committed=False`.
  - **Mutation Trace**: Order remains `paper_open`, `closed_at=None`, realized net PnL=None, exactly 0 ledger / journal / terminal outbox mutations.

### Bug 2: STRICT Context Compatibility Enforcement (PHẦN 003, 025-033, 054)
- **Before Fix**:
  - `validate_context_compatibility` accepted partial 4-field dictionaries (`order_id`, `symbol`, `direction`, `exit_policy_version`), omitting `context_schema_version`, `scan_cutoff`, `source_epoch`, `query_generation`, `arrival_revision`, `exit_context_fingerprint`.
  - Epoch/fingerprint/revision were only validated if present in both contexts. Missing keys defaulted to loose matching.
- **After Fix**:
  - `REQUIRED_CONTEXT_FIELDS` defines the exact 12 canonical fields:
    `context_schema_version`, `order_id`, `window_id`, `symbol`, `direction`, `opened_at`, `scan_cutoff`, `source_epoch`, `query_generation`, `arrival_revision`, `exit_policy_version`, `exit_context_fingerprint`.
  - `validate_context_schema(payload, mode='STRICT')` independently validates schema presence, non-emptiness, positive integer bounds (`epoch > 0`, `generation > 0`, `revision > 0`), non-boolean types, finite non-negative timestamps (`opened_at <= scan_cutoff` when `scan_cutoff > 0`), and deterministic fingerprints.
  - `validate_context_compatibility(stored, expected, migration_mode='STRICT')` validates both stored and expected schemas first; if either lacks required fields, it fails closed with `MISSING_FIELD_*`.
  - Legacy mode is an explicit authorized branch (`migration_mode='LEGACY'`) and is never applied by default.

### Bug 3: Custom NY Session Window Parity (PHẦN 004, 034-039, 051)
- **Before Fix**:
  - `TradingPolicyService.resolve_entry_session_context` called `TradingPolicyService.get_ny_session_window(now_ms=..., clock=...)` without passing `policy=policy`.
  - It defaulted to hardcoded `08:00 - 11:00 NY` regardless of custom policy settings (e.g. `09:00 - 12:00 NY`, fallback `11:30`).
  - At 11:30 NY under custom 09:00–12:00 policy, `get_ny_session_window(policy)` returned `in_window=True`, but `resolve_entry_session_context` returned `in_ny_entry_window=False`.
- **After Fix**:
  - `resolve_entry_session_context` explicitly passes `policy=policy` to `get_ny_session_window(now_ms=now_ms, clock=clock, policy=policy)`.
  - If `policy is None`, `resolve_entry_session_context` immediately returns a rejected context with `MISSING_POLICY`.
  - At 11:30 NY under 09:00–12:00: `in_ny_entry_window=True`, candidate is evaluated, armed, and filled; setup expiry correctly caps at 12:00 (not 11:00).
  - Outside window at 08:30 NY: `in_ny_entry_window=False`, candidate blocked by policy gate. Context, TTL expiry, and entry guard observe the identical configured window.

---

## 3. Production Three-Session Execution Evidence (PHẦN 005, 041-052)

Unlike prior detector-only tests (`evaluate_scenario_cycle`), V14.4.11 proves the complete production execution pipeline from market reading to actual fill:
`StrategyService.evaluate_market_reading_cycle` -> `WatchSetup(READY)` -> `_auto_arm_candidate` -> `PaperOrder(armed)` -> `ExecutionCoordinator.evaluate_orders_sync` -> `PaperOrder(paper_open)` -> DayAudit fills quota & outbox.

### Production Pipeline Trace Table

| Session | Direction | Timestamp (NY) | WatchSetup ID | PaperOrder ID | Planned Entry | Actual Entry | Fills Count | Result State |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **ASIA** | LONG | 2026-04-13 20:00 NY | `setup_asia_l_...` | `ord_asia_l_...` | 2650.00 | 2650.00 | 1 / 3 | `paper_open` |
| **ASIA** | SHORT | 2026-04-13 20:00 NY | `setup_asia_s_...` | `ord_asia_s_...` | 2650.00 | 2650.00 | 1 / 3 | `paper_open` |
| **LONDON** | LONG | 2026-04-14 04:00 NY | `setup_lon_l_...` | `ord_lon_l_...` | 2650.00 | 2650.00 | 1 / 3 | `paper_open` |
| **LONDON** | SHORT | 2026-04-14 04:00 NY | `setup_lon_s_...` | `ord_lon_s_...` | 2650.00 | 2650.00 | 1 / 3 | `paper_open` |
| **NEW YORK** | LONG | 2026-04-14 09:30 NY | `setup_ny_l_...` | `ord_ny_l_...` | 2650.00 | 2650.00 | 1 / 3 | `paper_open` |
| **NEW YORK** | SHORT | 2026-04-14 09:30 NY | `setup_ny_s_...` | `ord_ny_s_...` | 2650.00 | 2650.00 | 1 / 3 | `paper_open` |

### Negative & Guard Recheck Controls
- **EXEC07 (Auto OFF)**: `auto_enabled=False` creates `WatchSetup(state='READY')`, but produces exactly 0 armed orders and 0 fills.
- **EXEC08 (No-Trigger Quote)**: Armed order with planned entry 2650.00 supplied with Ask 2652.10 (Ask > Entry for Limit Buy) remains in `state='armed'` with fills count 0.
- **EXEC09 (Custom Window Parity)**: At 11:30 NY with 09:00–12:00 NY policy, order arms and fills; at 08:30 NY, order is blocked.
- **EXEC10 (Post-Fill Exit)**: Filled order supplied with exit quote at TP price triggers `TradeLifecycleService.execute_close`, updating order state to `closed` with canonical positive realized net PnL.
- **Guard Recheck**: Order armed during Asian session under `ALL_SESSIONS`; before trigger, policy updates to `NY_ONLY`. Coordinator rechecks entry policy gate upon trigger tick and rejects armed order with `POLICY_GATE`.

---

## 4. Test Suite Execution & Acceptance Results

### Acceptance Gates Summary (`acceptance_v14_4_2.json`)
```
============================= V14.4.2 ACCEPTANCE REPORT =============================
  backend_targeted       PASS  (445 passed, 1 warning, 32.68s)
  frontend_typecheck     PASS  (tsc -b, 5.35s)
  frontend_lint          PASS  (oxlint, 1.0s, 0 errors)
  frontend_vitest        PASS  (vitest junit, 2.97s)
  frontend_build         PASS  (npm run build, 6.74s)
  browser_e2e            PASS  (playwright test, 48 passed, 212.57s)
  benchmark              PASS  (p95=18.12ms <= 250ms, browser_p95=28.3ms, 9.77s)
  git_diff_check         PASS  (git diff --check, 0.06s)
  OVERALL                PASS  (Exit code 0)
=====================================================================================
```

### New Test Cases Manifest Coverage
- **A01** (`test_v14_4_11_reconciliation_authority.py`): Corrupt snapshot fails closed across all paths -> **PASS**
- **A02** (`test_v14_4_11_reconciliation_authority.py`): Strict context schema rejects missing fields and non-integer epochs -> **PASS**
- **A03** (`test_v14_4_11_reconciliation_authority.py`): Unverified legacy snapshots distinguished from corrupt snapshots -> **PASS**
- **A04** (`test_v14_4_11_reconciliation_authority.py`): Field mismatches and identity conflicts fail with precise diagnostic codes -> **PASS**
- **A05** (`test_v14_4_11_reconciliation_authority.py`): Frozen cutoff continuation, chronological ordering, and conservative tie resolution -> **PASS**
- **A06** (`test_v14_4_11_reconciliation_authority.py`): Persistence failure isolation and restart recovery -> **PASS**
- **EXEC01** (`test_v14_4_11_three_session_execution.py`): Asia LONG production pipeline READY -> ARM -> FILL -> **PASS**
- **EXEC02** (`test_v14_4_11_three_session_execution.py`): Asia SHORT production pipeline READY -> ARM -> FILL -> **PASS**
- **EXEC03** (`test_v14_4_11_three_session_execution.py`): London LONG production pipeline READY -> ARM -> FILL -> **PASS**
- **EXEC04** (`test_v14_4_11_three_session_execution.py`): London SHORT production pipeline READY -> ARM -> FILL -> **PASS**
- **EXEC05** (`test_v14_4_11_three_session_execution.py`): NY LONG production pipeline READY -> ARM -> FILL -> **PASS**
- **EXEC06** (`test_v14_4_11_three_session_execution.py`): NY SHORT production pipeline READY -> ARM -> FILL -> **PASS**
- **EXEC07** (`test_v14_4_11_three_session_execution.py`): Auto OFF promotes READY setup without arm or fill -> **PASS**
- **EXEC08** (`test_v14_4_11_three_session_execution.py`): No-trigger quote keeps order armed with zero fills -> **PASS**
- **EXEC09** (`test_v14_4_11_three_session_execution.py`): Custom NY window parity in and out of window -> **PASS**
- **EXEC10** (`test_v14_4_11_three_session_execution.py`): Post-fill exit execution path canonical close -> **PASS**

---

## 5. Scope & Preservation Verification (PHẦN 006, 007, 008, 076)
- **SMC Strategy Thresholds**: Preserved intact (no changes to sweep depths, displacement ratios, FVG confirmation, or R:R minimums).
- **Execution Invariants**: Single open position, single armed order, maximum 3 actual fills/day UTC+7, and NY reserve slot maintained across all tests.
- **Risk & Financial Geometry**: User risk settings and frozen order geometry remain immutable post-arm.
- **UI & Routes**: Frontend components, API routes, and chart layout preserved.
- **PAPER Environment Boundary**: PAPER testing verifies simulated execution and state machine transitions; it does not claim exchange live fills or Telegram hardware push receipts.
