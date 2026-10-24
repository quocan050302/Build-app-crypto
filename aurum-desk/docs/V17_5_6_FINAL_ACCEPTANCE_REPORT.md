# AURUM V17.5.6 — FINAL ACCEPTANCE REPORT
## Real Order Cost Parity, Protected Leg Proof & Mandatory Chart Geometry

**Release Version**: AURUM V17.5.6  
**Evaluation Date**: 2026-10-24 17:41:56 (Vietnam Local Time)  
**Target Repository**: `quocan050302/Build-app-crypto`  
**Target Branch**: `fix/v14-1-rr-flow`  
**Base Commit**: `a702460ccd4b7610e04b53921bd9b6296cf2af17`  
**Acceptance Verdict**: **ACCEPTED** (All 12 Gates Passed)

---

### 1. Executive Summary

AURUM V17.5.6 closes the remaining technical gaps from V17.5.5 with real production code modifications, new targeted regression test suites, and strict end-to-end browser assertions:

1. **Real Order Cost Parity (Group A)**:
   - Wired the single authoritative cost resolver (`resolve_execution_cost_context`) into `build_arm_plan_snapshot`, `_arm_paper_candidate`, and `ExecutionCoordinator.evaluate_orders_sync`.
   - Verified that explicit 0.0 slippage is strictly preserved across ARM and fill without reverting to 0.10.
   - Enforced snapshot-first authority: changes in system risk settings do not rewrite frozen costs of historical or active orders.
   - Hand-checked fill parity: Bid 2000.00, Ask 2000.20 with slippage 0.27 correctly fills MARKET LONG at 2000.47 and MARKET SHORT at 1999.73.

2. **Protected Leg Episode & Origin Proof (Group B)**:
   - `select_protected_origin_for_break` and `derive_protected_leg_episode` enforce mandatory `closed_bars` (empty bars fail closed with `MISSING_SOURCE_CANDLES`).
   - Selected reference is strictly consumed to bound the displacement break episode.
   - Standardized break time semantics: `break_open_ms` (bar open) vs `break_known_at_ms` (bar close) eliminates timestamp confusion between generic continuation and FVG continuation models.
   - Rejected invalid detector-specified origins without falling back to arbitrary pivots.

3. **Mandatory Finite Chart Geometry & Diagnostics (Group C)**:
   - `__AURUM_CHART_OVERLAY_DIAGNOSTICS__` is only attached when test mode is active (`isChartDiagnosticsEnabled()`), keeping production builds clean.
   - E2E helper `waitForReadyOverlayGeometry` polls until canvas coordinates are finite and unconditionally asserts `Number.isFinite(entryY, slY, tpY)` without any `if(...)` skip.
   - Validated standard canvas orientation: SHORT orders project `tpY > entryY > slY`; LONG orders project `slY > entryY > tpY`.

---

### 2. Before vs. After Comparison Matrix

| Area | Baseline (V17.5.5) | V17.5.6 Implementation | Evidence / Test Gate |
|---|---|---|---|
| **Cost Authority in Order Lifecycle** | Shared cost helper was only invoked by research comparison. ARM and ExecutionCoordinator resolved costs separately with fallback defaults. | Unified resolver (`resolve_execution_cost_context`) is called during ARM and read snapshot-first by `ExecutionCoordinator`. | `tests/test_v17_5_6_actual_order_cost_parity.py` (P081–P110) |
| **Explicit 0.0 Slippage** | Explicit 0.0 in snapshot could be overridden by 0.10 default in certain paths. | Explicit 0.0 is preserved across snapshot resolution, ARM snapshot, and fill execution. | `test_p081_explicit_zero_slippage_in_actual_arm_and_fill` |
| **Origin Candle Verification** | `_verify_pivot_source` returned `True` when `closed_bars` was empty. | `select_protected_origin_for_break` and `derive_protected_leg_episode` return `UNAVAILABLE` (`MISSING_SOURCE_CANDLES`) if `closed_bars` is empty. | `test_p114_empty_closed_bars_returns_unavailable` |
| **Episode Boundaries** | `selected_reference` was accepted as parameter but not actively consumed to bound the origin search segment. | Reference price and timestamp strictly bound the displacement episode; pivots outside leg are excluded. | `test_p111_p112_multiple_legs_and_distant_extreme` |
| **Break Timestamp Consistency** | Continuation passed `broken_at_ms = open + 60_000` while bridge interpreted it as break open cutoff. | Standardized: `break_open_ms = open`, `broken_at_ms = open`, `known_at_ms = open + 60_000`. | `tests/test_v17_5_6_break_time_contract.py` |
| **Chart Overlay Diagnostics** | Diagnostics bridge exposed unconditionally whenever `window` existed. | Diagnostics bridge only exposed when `isChartDiagnosticsEnabled()` is true (test mode). | `ChartComponent.tsx` & E2E P03 |
| **Browser Geometry Assertions** | Test had `if(geom && entryY !== null)` guard, allowing null geometry to bypass assertions. | `waitForReadyOverlayGeometry` polls for finite coordinates and unconditionally asserts `Number.isFinite(entryY, slY, tpY)`. | `v17_5_6_chart_evidence.e2e.ts` (P01, P02) |

---

### 3. Acceptance Execution Results

Executed via `tools/run_v17_5_6_acceptance.py`:

```
======================================================================
AURUM V17.5.6 ACCEPTANCE RESULT: ACCEPTED
Backend Tests: 192/192 passed across 8 gates (39 suites)
Frontend Vitest: 501 tests across 48 files passed
Frontend Build & Lint: PASS
Playwright Browser E2E: 5 tests passed
Artifact report saved: /Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/artifacts/v17_5_6/acceptance.json
======================================================================
```

#### Detailed Gate Breakdown

- **Gate 0 (V17.5.6 Core Targeted Suites)**: **PASS** (37 / 37 passed in 3.27s)
  - `test_v17_5_6_actual_order_cost_parity.py`: 12 passed
  - `test_v17_5_6_episode_origin_binding.py`: 11 passed
  - `test_v17_5_6_break_time_contract.py`: 8 passed
  - `test_v17_5_6_cycle_regression.py`: 6 passed
- **Gate 1 (V17.5.5 Core Suites)**: **PASS** (23 / 23 passed in 3.83s)
- **Gate 2 (V17.5.4 Target Suites)**: **PASS** (16 / 16 passed in 1.81s)
- **Gate 3 (V17.5.3 Boundary Probes & Cycle Review)**: **PASS** (17 / 17 passed in 2.00s)
- **Gate 4 (V17.5.2 Baseline Suites)**: **PASS** (7 / 7 passed in 1.81s)
- **Gate 5 (V17.5.1 Feature Suites & Probes)**: **PASS** (26 / 26 passed in 2.66s)
- **Gate 6 (Existing V17.5 Regression Suites)**: **PASS** (31 / 31 passed in 1.43s)
- **Gate 7 (Prior Regression Suites)**: **PASS** (35 / 35 passed in 5.79s)
- **Gate 8 (Frontend Vitest Unit Tests)**: **PASS** (501 / 501 passed across 48 files in 5.59s)
- **Gate 9 (Frontend Build - `npm run build`)**: **PASS** (in 6.98s)
- **Gate 10 (Frontend Lint - `npm run lint`)**: **PASS** (0 errors in 0.36s)
- **Gate 11 (Playwright Browser Verification - `v17_5_6_chart_evidence.e2e.ts`)**: **PASS** (5 / 5 passed in 16.28s)

---

### 4. Artifacts and Audit Trail

- **Acceptance Metadata**: `artifacts/v17_5_6/acceptance.json`
- **JUnit Reports**:
  - `artifacts/v17_5_6/junit/gate0_v17_5_6_core.xml`
  - `artifacts/v17_5_6/junit/gate1_v17_5_5_core.xml`
  - `artifacts/v17_5_6/junit/gate2_v17_5_4.xml`
  - `artifacts/v17_5_6/junit/gate3_v17_5_3.xml`
  - `artifacts/v17_5_6/junit/gate4_v17_5_2.xml`
  - `artifacts/v17_5_6/junit/gate5_v17_5_1.xml`
  - `artifacts/v17_5_6/junit/gate6_v17_5.xml`
  - `artifacts/v17_5_6/junit/gate7_prior.xml`
- **Execution Logs**: `artifacts/v17_5_6/logs/` (All 12 gate stdout/stderr outputs preserved)

---

### 5. Boundaries and Limitations

1. **Synthetic vs Empirical Reality**: All tests pass under simulated and recorded market conditions. Real-market gold (XAUUSDT) slippage, spread spikes during high-impact news, and liquidity gaps can exceed standard assumptions.
2. **Empirical Profitability is UNPROVEN**: Technical correctness of cost attribution and stop-loss placement does not guarantee positive expectancy.
3. **No Strategy Drift**: No ATR parameters, reward ratios, or entry filters were loosened to pass tests. Risk invariants (cap3/max1position) remain enforced.
