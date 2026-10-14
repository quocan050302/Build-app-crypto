# V14.4.10 THREE-SESSION ENTRY ALIGNMENT & FROZEN RECONCILIATION CONTEXT CLOSURE REPORT

## 1. Executive Summary & Verification Context

- **Repository**: `quocan050302/Build-app-crypto`
- **Branch**: `fix/v14-1-rr-flow`
- **Reviewer Target Commit**: `1ed60f4`
- **Implementation Baseline**: `ab3e845`
- **Date**: 2026-10-14
- **Core Mission**:
  1. **Three-Session Alignment for Standard SMC**: Eliminate false `OUTSIDE_ENTRY_WINDOW` rejections and premature setup expiry for Asian and London sessions under `ALL_SESSIONS_WITH_NY_RESERVE`, while preserving the 1-slot NY reserve target, max 3 fills/day UTC+7, strict entry guards, and restricting fallback search to the NY window.
  2. **Frozen Reconciliation Context Validation**: Ensure that stored reconciliation accumulator context matches the immutable frozen order plan geometry (symbol, direction, prices, policy versions, exit context fingerprint) so that mismatched context (`direction="SHORT"`, `symbol="BTCUSDT"`, `exit_policy_version="FOREIGN"`) triggers `REPLAY_NEEDED` instead of mutating orders to `CLOSE`.
  3. **Strict Non-Regression**: Full preservation of SMC detector thresholds, net RR math, risk budgets (0.10% cap on NY fallback), single open position, single armed order, CAS lifecycle transitions, and immutable accounting ledgers.

---

## 2. Root Cause Analysis of Addressed Defects

### Defect 1: Hardcoded NY Window Restriction & Truncated Setup Expiry
- **Symptom**: Under `ALL_SESSIONS_WITH_NY_RESERVE`, certified SMC setups detected during the Asian or London sessions received `OUTSIDE_ENTRY_WINDOW` in `evaluate_scenario_cycle` and were never promoted to `READY` or `ARM`. Even if evaluated after NY start, `promote_confirmed_scenario` capped setup expiry to `entry_window[1]`, causing setups outside NY to instantly expire (`expires_at <= now`).
- **Root Cause**:
  1. `evaluate_market_reading_cycle` unconditionally fetched `ny_window = TradingPolicyService.get_ny_session_window(...)` and passed `entry_window=(window_start_ms, window_end_ms)` down to `evaluate_scenario_cycle`.
  2. In `smc_scenario_service.py`, `_evaluate_direction` checked `if entry_window is not None: if not (window_start <= now <= window_end): blockers.append("OUTSIDE_ENTRY_WINDOW")`.
  3. In `evaluate_market_reading_cycle`, setup expiry was calculated as `expires_at_ms = min(now_ms + scenario_ttl_ms, entry_window[1]) if entry_window[1] > now_ms else now_ms`.
- **Solution**:
  - Introduced `EntrySessionContext` and `resolve_entry_session_context(policy, family, now_ms, clock=None)`.
  - For `STANDARD_SMC` under `ALL_SESSIONS_WITH_NY_RESERVE`, `standard_window_restriction = None` and `expiry_cap_ms = None`.
  - For `STANDARD_SMC` under `NY_ONLY` or `NY_QUOTA_PAPER`, `standard_window_restriction = (ny.start_ms, ny.end_ms)` and `expiry_cap_ms = ny.end_ms`.
  - Added pure `compute_promoted_setup_expiry(now_ms, scenario_expires_at, ttl_ms, session_context)` enforcing model expiry, scenario TTL, and applicable session caps without premature truncation.

### Defect 2: Loose Reconciliation Context Validation Permitting Mismatched Close
- **Symptom**: When `process_window_slice` was provided an accumulator or progress with stored `validation_context` containing a mismatched direction (`SHORT`), foreign symbol (`BTCUSDT`), or foreign exit policy, the baseline returned `CLOSE` and mutated the order to closed with empty reasons.
- **Root Cause**:
  1. `validate_accumulator_context` only compared `accumulator.window_id != window.window_id` and candidate order IDs; it never inspected `accumulator.validation_context`.
  2. `build_reconciliation_context` hardcoded `exit_policy_version = "1.0.0"` and did not bind the frozen order snapshot geometry (entry, SL, TP, exit version, fingerprint).
  3. `merge_candidate_accumulator_with_result` unconditionally stamped `validation_context=ctx` without verifying compatibility with the previously stored context.
- **Solution**:
  - Implemented `read_frozen_exit_context(order)` and deterministic `fingerprint_exit_context(ctx)`.
  - Implemented `validate_context_compatibility(stored, expected) -> ValidationResult` inspecting critical identity and policy fields (`order_id`, `window_id`, `symbol`, `direction`, `exit_context_fingerprint`, `exit_policy_version`, `schema_version`).
  - Updated `validate_accumulator_context` and `merge_candidate_accumulator_with_result` to reject mismatched contexts, flag `WINDOW_ID_MISMATCH` / `EVIDENCE_ID_CONFLICT`, mark `is_valid=False`, and trigger `REPLAY_NEEDED`.

---

## 3. Architecture & Implementation Map

| File / Component | Symbol Name | Status | Role / Responsibility |
|---|---|---|---|
| `backend/services/trading_policy_service.py` | `EntrySessionContext` | **MỚI** | Dataclass capturing policy mode, strategy family, session name, NY window bounds, standard window restriction, and expiry cap. |
| `backend/services/trading_policy_service.py` | `resolve_entry_session_context` | **MỚI** | Pure resolver translating policy + family + timestamp into `EntrySessionContext`. |
| `backend/services/trading_policy_service.py` | `compute_promoted_setup_expiry` | **MỚI** | Pure helper calculating `min(now + TTL, scenario.expires_at, [expiry_cap])` ensuring valid setup lifetime. |
| `backend/services/trading_policy_service.py` | `TradingPolicyService` | **HIỆN CÓ** | Authoritative policy validator maintaining `ALL_SESSIONS_WITH_NY_RESERVE`, quota reserves, and max daily fills. |
| `backend/services/smc_scenario_service.py` | `_evaluate_direction` | **HIỆN CÓ (SỬA)** | Evaluates `session_context.standard_window_restriction`. Under all-sessions, skips false NY window block. |
| `backend/services/smc_scenario_service.py` | `evaluate_scenarios` | **HIỆN CÓ (SỬA)** | Accepts `session_context: Optional[Any] = None` and propagates to direction evaluation. |
| `backend/services/smc_scenario_service.py` | `evaluate_scenario_cycle` | **HIỆN CÓ (SỬA)** | Accepts `session_context: Optional[Any] = None` and resolves effective restriction. |
| `backend/services/strategy_service.py` | `evaluate_market_reading_cycle` | **HIỆN CÓ (SỬA)** | Resolves `EntrySessionContext`, propagates restriction to scenario cycle, computes expiry via `compute_promoted_setup_expiry`. |
| `backend/services/market_reconciliation_service.py` | `FrozenExitContextResult` | **MỚI** | Dataclass holding validated frozen geometry from order snapshot. |
| `backend/services/market_reconciliation_service.py` | `canonical_num` | **MỚI** | Normalizes numeric coordinates into canonical strings (`4200.0` -> `"4200"`). |
| `backend/services/market_reconciliation_service.py` | `fingerprint_exit_context` | **MỚI** | SHA-256 deterministic hash of immutable exit geometry. |
| `backend/services/market_reconciliation_service.py` | `read_frozen_exit_context` | **MỚI** | Canonical extractor of frozen order snapshot plan. |
| `backend/services/market_reconciliation_service.py` | `validate_context_compatibility` | **MỚI** | Strict comparator of stored vs expected reconciliation contexts. |
| `backend/services/market_reconciliation_service.py` | `build_reconciliation_context` | **HIỆN CÓ (SỬA)** | Incorporates frozen exit context and fingerprint. |
| `backend/services/market_reconciliation_service.py` | `validate_candidate_context` | **HIỆN CÓ (SỬA)** | Rejects candidates beyond scan cutoff or with non-finite prices. |
| `backend/services/market_reconciliation_service.py` | `validate_accumulator_context` | **HIỆN CÓ (SỬA)** | Compares `accumulator.validation_context` against expected context using `validate_context_compatibility`. |
| `backend/services/market_reconciliation_service.py` | `merge_candidate_accumulator_with_result` | **HIỆN CÓ (SỬA)** | Rejects incompatible context on merge and detects conflicting duplicate evidence IDs. |

---

## 4. Test Suite Matrix (E01–E10 & C01–C10)

### Entry Flow Suite: `backend/tests/test_v14_4_10_three_session_entry.py` (10/10 PASS)
- **E01 (`test_e01_entry01_asia_promoted_and_filled`)**: Certified Asian setup under `ALL_SESSIONS_WITH_NY_RESERVE` has no `OUTSIDE_ENTRY_WINDOW` blocker, promotes to `READY`, ARMs when Auto=True, and fills on matching quote (quota 0 -> 1).
- **E02 (`test_e02_entry02_london_short_flow`)**: Certified London SHORT setup promotes, ARMs, and fills with correct geometry (`TP < Entry < SL`). Unreached limit price does not fill.
- **E03 (`test_e03_entry03_ny_only_mode_blocks_asia_london`)**: Under `NY_ONLY`, identical Asian and London setups are blocked by `OUTSIDE_ENTRY_WINDOW` and do not ARM or fill.
- **E04 (`test_e04_entry04_ny_slot_reservation_at_quota_limit`)**: Total daily fills = 2 and NY fills = 0 blocks outside-NY entries with `NY_SLOT_RESERVED`. NY entry is allowed. At total fills = 3, all entries blocked.
- **E05 (`test_e05_entry05_auto_off_creates_ready_not_armed`)**: With Auto=False, setups promote to `READY` but do not auto-ARM or fill. Enabling Auto in next cycle ARMs setup. Protective exits for open positions remain active with Auto off.
- **E06 (`test_e06_entry06_exact_model_and_cap_ttl`)**: Standard setups in Asia/London do not cap expiry to NY end. Fallback and NY_ONLY setups correctly cap expiry to NY window end.
- **E07 (`test_e07_entry07_guard_negative_matrix`)**: Active positions, red news blackout, red lessons, stale feeds, and poor net RR (< 1.5) strictly block ARM and FILL in Asia/London.
- **E08 (`test_e08_entry08_ny_fallback_isolated_to_ny_window`)**: NY fallback is rejected in Asia and London and outside the 10:30–11:00 NY window. Hard 0.10% risk cap preserved.
- **E09 (`test_e09_entry09_dst_and_midnight_boundary`)**: Summer (EDT) and Winter (EST) NY window transitions resolve correctly via `zoneinfo`. Fills across 23:59:59 UTC+7 cleanly advance quota days without double-counting.
- **E10 (`test_e10_entry10_full_chain_causality`)**: Formations waiting for sweeps or retests remain in observation states (`WAITING_SWEEP`, `WAITING_RETEST`). Future candle append does not alter historical decisions.

### Frozen Context Suite: `backend/tests/test_v14_4_10_frozen_context.py` (10/10 PASS)
- **C01 (`test_c01_ctx01_repro_wrong_context_does_not_close`)**: Stored context with foreign symbol, reversed direction, or foreign exit policy triggers `REPLAY_NEEDED`. Order remains `paper_open`, 0 PnL, no terminal notifications.
- **C02 (`test_c02_ctx02_fingerprint_geometry_validation`)**: Mismatched SL/TP geometry generates distinct fingerprints and is rejected as incompatible. Global risk setting changes do not alter frozen historical fingerprints.
- **C03 (`test_c03_ctx03_revision_and_epoch_mismatch`)**: Query generation and epoch mismatches are detected and flagged.
- **C04 (`test_c04_ctx04_frozen_cutoff_preservation`)**: Progress preserves frozen scan cutoff; current time advances do not cause spurious context invalidations. Candidates beyond frozen cutoff are rejected.
- **C05 (`test_c05_ctx05_schema_and_legacy_unsupported`)**: Missing or unsupported schemas fail closed. Legacy records without frozen snapshots require review rather than silent mutation.
- **C06 (`test_c06_ctx06_duplicate_evidence_conflict`)**: Identical evidence IDs deduplicate idempotently; conflicting payloads generate `EVIDENCE_ID_CONFLICT` and mark accumulator invalid.
- **C07 (`test_c07_ctx07_direct_resolver_apply_defenses`)**: Direct calls to `resolve_reconciliation_window` and `persist_resolution_or_review` reject foreign candidates and invalid prices. Closed orders reject conflicting historical rewrites.
- **C08 (`test_c08_ctx08_persist_failure_rollback`)**: Simulated SQLite commit failure rolls back transaction cleanly without partial state corruption.
- **C09 (`test_c09_ctx09_hard_read_budget_regression`)**: V14.4.9 budget invariants preserved: bounded query limit, empty page costs, global consumption tracking.
- **C10 (`test_c10_ctx10_earliest_tp_and_51_audits_regression`)**: Earliest TP detection preserved; 51 unique evidence audits consumed without starvation or state corruption.

---

## 5. Flow Preservation & System Invariants

1. **SMC Detection Math & Thresholds**:
   - Zero changes to swing detection, displacement thresholds, FVG detection, or retest criteria.
2. **Risk Management & Caps**:
   - Canonical risk settings and net RR calculations preserved.
   - NY fallback hard risk cap strictly maintained at min(0.10%, configured risk).
3. **Execution & Order Invariants**:
   - Single open position invariant strictly maintained.
   - Single armed order invariant strictly maintained.
   - Quota limit: exactly max 3 fills/day UTC+7.
   - Reserved NY slot: outside NY entries blocked if daily fills = 2 and NY fills = 0.
4. **Protective Exits**:
   - Protective TP/SL exits operate 24/7 across all sessions, regardless of Auto setting, quota limits, or red news.
5. **Ledger & Accounting**:
   - All PnL, commission, and margin calculations execute through canonical calculator via CAS optimistic locking in `TradeLifecycleService`.
   - Invalid reconciliation context causes zero database mutations and zero outbox events.

---

## 6. Before -> After Concrete Traces

### Trace 1: Asian/London Certified Setup Entry Flow
* **Before (Baseline `ab3e845` / `1ed60f4`)**:
  1. `evaluate_market_reading_cycle` fetched NY session window `(window_start_ms, window_end_ms)`.
  2. Passed `entry_window=(ny_start, ny_end)` to `evaluate_scenario_cycle`.
  3. In `_evaluate_direction`, check `entry_window[0] <= now_ms < entry_window[1]` failed during Asian (20:00 NY) or London (04:00 NY) sessions.
  4. Snapshot received `blockers=["OUTSIDE_ENTRY_WINDOW"]` and state remained `STRUCTURE_CONFIRMED` with blockers, never promoted to `READY` or `ARM`.
  5. If `promote_confirmed_scenario` was called, `expires_at_ms = min(now_ms + 45min, entry_window[1]) if entry_window[1] > now_ms else now_ms` truncated `expires_at` to `now_ms` (instant expiry).
* **After (V14.4.10)**:
  1. `evaluate_market_reading_cycle` calls `resolve_entry_session_context(policy, "STANDARD_SMC", now_ms)`.
  2. For `ALL_SESSIONS_WITH_NY_RESERVE`, `standard_window_restriction = None` and `expiry_cap_ms = None`.
  3. `_evaluate_direction` checks effective restriction `None` -> No `OUTSIDE_ENTRY_WINDOW` blocker.
  4. Setup cleanly promotes to `READY` with valid `expires_at = now_ms + scenario_ttl_ms` (e.g. 45 min).
  5. StrategyService `_auto_arm_candidate` checks entry guards (news, lessons, risk, account, quote, feed) -> setup ARMs successfully.
  6. Matching quote triggers CAS transition to `paper_open`, increments daily fill counter (0 -> 1) without violating NY reserve.

### Trace 2: Setup Expiry TTL Boundaries
* **Before**:
  - Expiry unconditionally capped to `entry_window[1]`.
  - Asian setups evaluated after NY session close received `expires_at = now_ms`, rejecting immediately.
* **After**:
  - `compute_promoted_setup_expiry` evaluates `min(now + TTL, scenario.expires_at)`.
  - Cap to `expiry_cap_ms` only applies when policy explicitly enforces a window restriction (`NY_ONLY` or `NY_QUOTA_PAPER`).
  - Standard SMC under all-sessions retains complete 45-minute lifecycle.

### Trace 3: Reconciliation Context Mismatch Defense
* **Before**:
  1. Order `ord_c01` exists: LONG XAUUSDT (Entry 4180, SL 4170, TP 4200).
  2. Evidence accumulator loaded with `validation_context`: `direction="SHORT"`, `symbol="BTCUSDT"`, `exit_policy_version="FOREIGN"`.
  3. `validate_accumulator_context` only verified `accumulator.window_id == window.order_id`.
  4. `process_window_slice` called `resolve_reconciliation_window` and executed close on candidate with `action="CLOSE"`, closing the order with $0 PnL and no mismatch explanation.
* **After**:
  1. `validate_accumulator_context` invokes `validate_context_compatibility(accumulator.validation_context, expected_context)`.
  2. Mismatch identified on `symbol` (`BTCUSDT` != `XAUUSDT`), `direction` (`SHORT` != `LONG`), and `fingerprint`.
  3. Accumulator marked invalid (`is_valid=False`, `reasons=["CONTEXT_SYMBOL_MISMATCH", "CONTEXT_DIRECTION_MISMATCH", "CONTEXT_FINGERPRINT_MISMATCH"]`).
  4. `process_window_slice` returns `decision=None`, `read_complete=False`, triggering `REPLAY_NEEDED`.
  5. Order remains `paper_open`, 0 ledger mutation, 0 outbox events.

---

## 7. Actual Verification Commands, Test Counts & Acceptance Gate Results

### Backend Targeted Pytest Matrix (45 Files, 407 Tests)
```powershell
$env:TESTING="1"; $env:PYTHONPATH=".;tests"
.\venv\Scripts\python.exe -m pytest tests/test_v14_4_2_financial_context_regression.py tests/test_v14_4_2_provisional_plan_regression.py tests/test_v14_4_2_requirements.py tests/test_v14_4_2_forecast_requirements.py tests/test_v14_4_2_entry_regression.py tests/test_v14_4_2_acceptance_runner.py tests/test_v14_4_1_production_pipeline.py tests/test_v14_4_1_contracts.py tests/test_v14_3_ny_quota.py tests/test_v14_3_frequency_audit.py tests/test_realtime_source_ordering.py tests/test_realtime_execution_worker.py tests/test_forecast_forward_observation.py tests/test_migration_runtime_upgrade.py tests/test_forecast_causal_closure.py tests/test_entry_intent_closure.py tests/test_order_snapshot_closure.py tests/test_realtime_closure.py tests/test_late_source_chronology.py tests/test_order_geometry_parity.py tests/test_order_binding_integrity.py tests/test_final_reconciliation_chronology.py tests/test_final_display_plan_sources.py tests/test_final_forecast_proof_pipeline.py tests/test_v14_4_4_policy_consistency.py tests/test_v14_4_4_ny_quota_pipeline.py tests/test_v14_4_4_reconciliation_runtime.py tests/test_v14_4_4_canonical_plan_runtime.py tests/test_v14_4_4_forecast_runtime.py tests/test_v14_4_4_metric_gate.py tests/test_v14_4_5_policy_startup.py tests/test_v14_4_5_reconciliation_window.py tests/test_v14_4_5_frozen_legacy_sources.py tests/test_v14_4_5_forecast_production.py tests/test_v14_4_5_acceptance_identity.py tests/test_v14_4_6_strict_proof_producer.py tests/test_v14_4_6_forecast_state_transitions.py tests/test_v14_4_6_pagination_replay.py tests/test_v14_4_6_flow_preservation.py tests/test_v14_4_7_proof_fail_closed.py tests/test_v14_4_7_coordinator_pagination.py tests/test_v14_4_8_reconciliation_accumulator.py tests/test_v14_4_9_reconciliation_integrity.py tests/test_v14_4_10_three_session_entry.py tests/test_v14_4_10_frozen_context.py -q
```
* **Result**: **407 PASSED**, 1 warning in 28.88s (0 failed, 0 skipped, 0 xfail).
  - V14.4.9 Baseline: 43 files, 387 passed.
  - V14.4.10 Addition: +2 files (`test_v14_4_10_three_session_entry.py`, `test_v14_4_10_frozen_context.py`), +20 tests.
  - Total: 45 files, 407 passed.

### Frontend Quality Verification Matrix
* **Typecheck (`tsc -b`)**: Exit code 0 (0 errors) in 6.1s.
* **Linter (`oxlint`)**: Exit code 0 (0 errors, 29 warnings) in 1.0s.
* **Vitest Suite (`vitest run`)**: Exit code 0 (26 files, 226 passed) in 1.35s.
* **Production Build (`vite build`)**: Exit code 0 (built in 1.47s).
* **Git Whitespace / Diff Check (`git diff --check`)**: Clean (0 errors).

---

## 8. Operational Deliverables & Verification Statement

1. **Runtime Source Files Modified**:
   - `backend/services/trading_policy_service.py`: Added `EntrySessionContext`, `resolve_entry_session_context`, `compute_promoted_setup_expiry`.
   - `backend/services/smc_scenario_service.py`: Added `session_context` propagation in `_evaluate_direction`, `evaluate_scenarios`, and `evaluate_scenario_cycle`.
   - `backend/services/strategy_service.py`: Wired `session_context` resolution and `compute_promoted_setup_expiry` in `evaluate_market_reading_cycle`.
   - `backend/services/market_reconciliation_service.py`: Added `FrozenExitContextResult`, `read_frozen_exit_context`, `fingerprint_exit_context`, `validate_context_compatibility`, `validate_candidate_context` (scan cutoff check, finite price check), `validate_accumulator_context` (strict compatibility), `merge_candidate_accumulator_with_result` (compatibility & duplicate conflict detection).
2. **Tool Runner Alignment**:
   - `tools/run_v14_4_2_acceptance.py`: Registered `test_v14_4_10_three_session_entry.py` (ENTRY01-ENTRY10) and `test_v14_4_10_frozen_context.py` (C01-C10) in `REQUIRED_CASES` and `targeted_files`.
3. **Dedicated Test Suites**:
   - `backend/tests/test_v14_4_10_three_session_entry.py`: 10 comprehensive tests for Asian, London, and NY session entry flows, slot reserve, TTL, Auto off, guards negative, fallback boundaries, and DST.
   - `backend/tests/test_v14_4_10_frozen_context.py`: 10 comprehensive tests for context mismatch repro, fingerprint geometry, revision/epoch validation, frozen cutoffs, legacy fallback, duplicate evidence conflict, direct caller defenses, persist failure rollback, and budget regression.
4. **Non-Regression & Safety**:
   - No mock bypasses in production pipelines.
   - No live database modification; all tests run against isolated in-memory/temp SQLite.
   - Zero modifications to SMC detection math or thresholds.

