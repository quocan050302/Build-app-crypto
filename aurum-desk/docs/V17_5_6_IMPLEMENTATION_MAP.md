# AURUM V17.5.6 — IMPLEMENTATION MAP
## Real Order Cost Parity, Protected Leg Proof & Mandatory Chart Geometry

**Release Target**: AURUM V17.5.6  
**Base Commit**: `a702460ccd4b7610e04b53921bd9b6296cf2af17`  
**Branch**: `fix/v14-1-rr-flow`  
**Execution Timestamp**: 2026-10-24 17:41:56 (Vietnam Time)  
**Overall Verdict**: **ACCEPTED** (All 12 Gates Passed: 192 Backend Tests, 501 Vitest Unit Tests, 5 Playwright Browser Tests)

---

### 1. Scope & Architectural Boundaries

This patch strictly closes technical gaps across three core technical areas:
- **Group A: Execution Cost Authority & Order Parity**: Unified cost authority (`resolve_execution_cost_context`) wired into `build_arm_plan_snapshot`, `_arm_paper_candidate`, and `ExecutionCoordinator.evaluate_orders_sync`. Frozen snapshots are authoritative for existing orders; explicit 0.0 slippage is preserved; dict/object costs are normalized without `getattr` fallback; no silent fallback to default 0.10.
- **Group B: Origin & Episode Proof**: Strict verification that protected swings originate within the displacement episode bounded by the reference and break bar. Non-empty `closed_bars` is mandatory (empty returns `UNAVAILABLE` / `MISSING_SOURCE_CANDLES`); break open (`break_open_ms`) and break known (`break_known_at_ms`) are strictly distinguished; pivots occurring at/after break open are excluded; specified origins fail closed without fallback.
- **Group C: Chart Geometry & Test Mode Diagnostics**: Chart diagnostics bridge (`__AURUM_CHART_OVERLAY_DIAGNOSTICS__`) is guarded by `isChartDiagnosticsEnabled()` (using `VITE_ENABLE_CHART_DIAGNOSTICS`); instance-owned cleanup prevents leaking bridges across chart mounts; Playwright browser tests enforce strictly finite canvas coordinates (`Number.isFinite(entryY, slY, tpY)`) with no conditional `if(...)` skipping.

---

### 2. Exact Function & Caller Implementation Map

#### Group A: Real Order Cost Parity

| Function / Symbol | File | Role & Modifications | Callers | Key Inputs / Outputs | Associated Tests |
|---|---|---|---|---|---|
| `resolve_execution_cost_context` | `backend/services/plan_financial_context_service.py` | Added normalization for dict/object snapshot, dict/object `settings["costs"]` with support for both `entry_slippage_usd` and `slippage_usd`. Strict finite non-negative checks on fee rates and slippage. Preserves version and provenance. | `build_arm_plan_snapshot`, `_arm_paper_candidate`, `ExecutionCoordinator.evaluate_orders_sync`, `resolve_stop_comparison_execution_context` | In: `candidate`, `snapshot`, `settings`, `config`, `as_of_ms`<br>Out: dict with `available`, `reason`, `entry_slippage_usd`, `tp_slippage_usd`, `sl_slippage_usd`, `maker_fee_rate`, `taker_fee_rate`, `version`, `provenance` | `tests/test_v17_5_6_actual_order_cost_parity.py` (P081-P090) |
| `build_arm_plan_snapshot` | `backend/services/order_plan_snapshot_service.py` | Added keyword argument `resolved_execution_costs: Optional[Dict[str, Any]] = None`. When provided, extracts frozen costs and provenance. Canonical snapshot sets `slippage_usd`, `entry_slippage_usd`, `tp_slippage_usd`, `sl_slippage_usd`, `maker_fee_rate`, `taker_fee_rate`, `cost_provenance`, `cost_version`, and `resolved_execution_costs`. Preserves backward compatibility. | `_arm_paper_candidate`, test fixtures | In: calculation results, order parameters, `resolved_execution_costs`<br>Out: `OrderPlanSnapshot` dictionary | `tests/test_v17_5_6_actual_order_cost_parity.py` (P081-P084, P089-P090) |
| `_arm_paper_candidate` | `backend/services/scalp_watcher_orchestrator.py` | Resolves authoritative cost context via `resolve_execution_cost_context(candidate=cand, settings=policy_settings, config=config, as_of_ms=now_ms)`. Passes resolved costs directly to `build_arm_plan_snapshot`. | `evaluate_cycle`, orchestrator dispatch | In: `cand`, `policy_settings`, `config`, `now_ms`<br>Out: armed `PaperOrder` with frozen authoritative snapshot | `tests/test_v17_5_5_cycle_to_chart_contract.py`, `tests/test_v17_5_6_actual_order_cost_parity.py` |
| `ExecutionCoordinator.evaluate_orders_sync` | `backend/services/execution_coordinator.py` | Snapshot-first resolution: reads `order_cost_snap` and calls `resolve_execution_cost_context(snapshot=order_cost_snap)`. When available, uses verified frozen slippage (including explicit 0.0 and custom slippage like 0.27). Retains fallback for unverified legacy records. | Order polling loop, test runner | In: DB session, quotes, clock<br>Out: evaluated and filled orders | `tests/test_v17_5_6_actual_order_cost_parity.py` (P081-P082, P084, P091-P100) |

#### Group B: Protected Leg Episode & Origin Proof

| Function / Symbol | File | Role & Modifications | Callers | Key Inputs / Outputs | Associated Tests |
|---|---|---|---|---|---|
| `ProtectedLegEpisode` | `backend/services/stop_placement_types.py` | Dataclass representing the displacement break episode: `status`, `model_id`, `direction`, `reference_id`, `reference_price`, `break_open_ms`, `break_known_at_ms`, `eligible_origin_ids`, `source_bar_ids`, `reasons`. | `derive_protected_leg_episode`, `select_protected_origin_for_break` | In: fields<br>Out: immutable episode object with `.to_dict()` | `tests/test_v17_5_6_episode_origin_binding.py`, `tests/test_v17_5_6_break_time_contract.py` |
| `validate_protected_origin_binding` | `backend/services/stop_placement_service.py` | Pure validator checking: non-empty closed bars, origin occurred strictly before break open, known at or before decision, within episode start boundary, source candle matching price extreme within `tick_size` tolerance. | `derive_protected_leg_episode`, `select_protected_origin_for_break` | In: `origin`, `direction`, `break_open_ms`, `closed_bars`, `tick_size`, etc.<br>Out: `Tuple[bool, Optional[str]]` | `tests/test_v17_5_6_episode_origin_binding.py` (P115, P116), `tests/test_v17_5_6_break_time_contract.py` (P122-P125) |
| `derive_protected_leg_episode` | `backend/services/stop_placement_service.py` | Constructs displacement episode from reference and break bar. Enforces: non-empty closed bars, reference close-through validation (no equality, no wick-only), break timestamps (`break_open_ms` = bar open, `break_known_at_ms` = bar close), filters candidate pivots by episode membership. | `select_protected_origin_for_break` | In: `model_id`, `direction`, `selected_reference`, `selected_break`, `confirmed_swings`, `closed_bars`, `decision_cutoff_ms`<br>Out: `ProtectedLegEpisode` | `tests/test_v17_5_6_episode_origin_binding.py` (P111-P114), `tests/test_v17_5_6_break_time_contract.py` (P121-P130) |
| `select_protected_origin_for_break` | `backend/services/stop_placement_service.py` | Invokes `derive_protected_leg_episode`. Enforces mandatory `closed_bars` (fails with `MISSING_SOURCE_CANDLES` if empty). Rejects detector-specified invalid origin without fallback. Returns deterministic best origin. | `resolve_bound_stop_leg`, `resolve_stop_anchor`, M1 Continuation models | In: `model_id`, `direction`, `selected_break`, `selected_reference`, `confirmed_swings`, `closed_bars`<br>Out: dictionary with `status`, `origin_pivot_id`, `origin_price`, `episode`, `reason` | `tests/test_v17_5_6_episode_origin_binding.py` (P111-P120), `tests/test_v17_5_5_origin_bridge.py` |
| `evaluate_m1_continuation_candidate` & `evaluate_m1_fvg_continuation_candidate` | `backend/services/scalp_m1_model_service.py` | Standardized break timestamp descriptors: explicitly sets `break_open_ms = break_bar_ts`, `broken_at_ms = break_bar_ts`, and `known_at_ms = break_bar_ts + 60_000` to eliminate 1-bar timestamp discrepancy. | Candidate evaluation loop | In: candles, quotes, policy settings<br>Out: `CandidateEvaluationResult` | `tests/test_v17_5_5_cycle_to_chart_contract.py`, `tests/test_v17_5_6_break_time_contract.py` (P131-P138) |

#### Group C: Mandatory Finite Chart Geometry & Diagnostics

| Function / Symbol | File | Role & Modifications | Callers | Key Inputs / Outputs | Associated Tests |
|---|---|---|---|---|---|
| `isChartDiagnosticsEnabled` | `frontend/src/ChartComponent.tsx` | Pure flag checking `import.meta.env.VITE_ENABLE_CHART_DIAGNOSTICS === 'true'` or `(window as any).__AURUM_ENABLE_CHART_DIAGNOSTICS__ === true`. Diagnostics bridge is only attached if this returns true. | `ChartComponent` mounting effect | In: none (reads env/window)<br>Out: boolean | `frontend/tests/e2e/v17_5_6_chart_evidence.e2e.ts` (P03) |
| `__AURUM_CHART_OVERLAY_DIAGNOSTICS__` | `frontend/src/ChartComponent.tsx` | Read-only bridge exposing `hasPrimitive`, `primitiveGeneration`, `data`, `geometry` (`entryY`, `slY`, `tpY`), `geometryStatus`, and `isFinite`. Tagged with `chartInstanceIdRef` so unmounting only cleans up its own instance bridge. | Playwright browser E2E test | In: none<br>Out: read-only diagnostics snapshot | `frontend/tests/e2e/v17_5_6_chart_evidence.e2e.ts` (P01-P04) |
| `waitForReadyOverlayGeometry` | `frontend/tests/e2e/v17_5_6_chart_evidence.e2e.ts` | E2E helper polling `__AURUM_CHART_OVERLAY_DIAGNOSTICS__` until coordinates are finite. Unconditionally asserts `Number.isFinite(entryY, slY, tpY)`. | E2E test cases P01, P02, P04 | In: `page`, `expectedOrderId`<br>Out: diagnostic snapshot | `frontend/tests/e2e/v17_5_6_chart_evidence.e2e.ts` |
| `playwright.config.ts` | `frontend/playwright.config.ts` | Added `VITE_ENABLE_CHART_DIAGNOSTICS: 'true'` to `webServer.env` to allow test mode diagnostics exclusively during Playwright test runs. | Playwright runner | In: configuration<br>Out: test server environment | Playwright E2E suite |

---

### 3. Verification Artifacts & Test Coverage Matrix

| Test Suite File | Type | Gates Covered | Pass Count | Status |
|---|---|---|---|---|
| `tests/test_v17_5_6_actual_order_cost_parity.py` | Backend Pytest | Gate 0 (Parts 081–110) | 12 / 12 | PASS |
| `tests/test_v17_5_6_episode_origin_binding.py` | Backend Pytest | Gate 0 (Parts 111–120, 141–150) | 11 / 11 | PASS |
| `tests/test_v17_5_6_break_time_contract.py` | Backend Pytest | Gate 0 (Parts 121–140) | 8 / 8 | PASS |
| `tests/test_v17_5_6_cycle_regression.py` | Backend Pytest | Gate 0 (Parts 151–160) | 6 / 6 | PASS |
| Baseline Suites (Gates 1–7) | Backend Pytest | 35 Suites (V17.5.5 down to baseline) | 155 / 155 | PASS |
| **Total Backend Tests** | **Pytest** | **Gates 0–7 (39 Suites)** | **192 / 192** | **PASS** |
| Frontend Unit Tests | Vitest | Gate 8 (48 Suites) | 501 / 501 | PASS |
| Frontend Build & Lint | TypeScript / ESLint / Vite | Gates 9 & 10 | 0 errors | PASS |
| `tests/e2e/v17_5_6_chart_evidence.e2e.ts` | Playwright E2E | Gate 11 (Parts 061–070, 161–190) | 5 / 5 | PASS |

---

### 4. Invariant Protection Summary

1. **Deterministic Execution**: No live DB or real Telegram calls in tests. All tests use in-memory SQLite and ReplayClock.
2. **Order Immutability**: Existing armed, open, or closed orders retain their frozen cost and stop loss snapshots.
3. **Fail-Closed Principle**: Missing or invalid source candles, non-finite costs, or invalid geometry strictly reject orders without synthetic fallback.
4. **Empirical Note**: Passing technical tests proves causal and geometric correctness under paper trading assumptions. Empirical profitability on live markets remains **UNPROVEN**.
