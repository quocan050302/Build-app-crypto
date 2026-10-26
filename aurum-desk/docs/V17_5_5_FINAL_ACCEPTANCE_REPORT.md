# AURUM V17.5.5 — FINAL ACCEPTANCE REPORT

**Branch:** `fix/v14-1-rr-flow`  
**Base Commit:** `8e882a4a5081924b6c0a95ad744f59131ea30b29` (`fix(v17.5.4): bind production research context and verify stop evidence`)  
**Target Commit:** `fix(v17.5.5): enforce research entry context and verify origin chart binding`  
**Execution Timestamp:** 2026-10-24T10:00:44Z  
**Technical Status:** `IMPLEMENTATION_VERIFIED`  
**Empirical Status:** `EMPIRICAL_UNPROVEN`  

---

## 1. Executive Summary

AURUM V17.5.5 delivers the final closure patch enforcing strict entry gate controls on research shadow variants, establishing unified maker/taker fee and slippage cost authority, bridging protected origin swing validation to break events and stop anchors, and verifying lightweight-chart canvas geometry on real Chrome browsers.

All 11 technical gates were executed under authoritative runner `tools/run_v17_5_5_acceptance.py`. Every test count was dynamically parsed from JUnit XML and test runner outputs without hardcoded numbers.

- **Backend Pytest:** 155 / 155 PASSED across 7 technical gates (0 failures).
- **Frontend Vitest:** 501 / 501 PASSED across 48 test files.
- **Frontend Build & Lint:** PASSED with 0 errors.
- **Playwright Browser E2E:** 4 / 4 PASSED with real primitive inspection, geometry calculations, selection switching, and mobile viewport assertions.
- **Verdict:** **ACCEPTED**

---

## 2. Technical Gate Verification Matrix

| Gate | Description | Test Targets | Result | Duration | Log File |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Gate 1** | V17.5.5 Core Suites | `test_v17_5_5_entry_context_gate.py`<br>`test_v17_5_5_cost_authority.py`<br>`test_v17_5_5_origin_bridge.py`<br>`test_v17_5_5_cycle_to_chart_contract.py` | **23 / 23 PASS** | 1.32s | `artifacts/v17_5_5/logs/gate1_v17_5_5_core.log` |
| **Gate 2** | V17.5.4 Target Suites | `test_v17_5_4_context_binding.py`<br>`test_v17_5_4_cost_parity.py`<br>`test_v17_5_4_anchor_proof.py`<br>`test_v17_5_4_cycle_reconciliation.py` | **16 / 16 PASS** | 1.33s | `artifacts/v17_5_5/logs/gate2_v17_5_4.log` |
| **Gate 3** | V17.5.3 Probes & Cycle Review | `test_v17_5_3_boundary_probes.py`<br>`test_v17_5_3_cycle_to_review.py` | **17 / 17 PASS** | 1.38s | `artifacts/v17_5_5/logs/gate3_v17_5_3.log` |
| **Gate 4** | V17.5.2 Baseline Suites | `test_v17_5_2_boundary_probes.py`<br>`test_v17_5_2_detector_to_exit.py` | **7 / 7 PASS** | 1.20s | `artifacts/v17_5_5/logs/gate4_v17_5_2.log` |
| **Gate 5** | V17.5.1 Feature Suites & Probes | 9 feature test suites (`red_probes`, `anchor_leg`, `structural_gate`, `atr_timeframe`, `policy_cas`, `research_runtime`, `production_flow`, `guard_matrix`, `stop_costs`) | **26 / 26 PASS** | 1.92s | `artifacts/v17_5_5/logs/gate5_v17_5_1.log` |
| **Gate 6** | Existing V17.5 Suites | 9 stop placement suites (`stop_placement`, `anchor_binding`, `risk_invariants`, `policy_api`, `exit_evidence`, `model_integration`, `production_flow`, `stop_review`, `stop_research`) | **31 / 31 PASS** | 1.30s | `artifacts/v17_5_5/logs/gate6_v17_5.log` |
| **Gate 7** | Prior Regressions | CG01, sessions roundtrip, atomic mirrors, baseline probes, noop authority | **35 / 35 PASS** | 3.44s | `artifacts/v17_5_5/logs/gate7_prior.log` |
| **Gate 8** | Frontend Vitest Suites | Unit and contract suites across all components, hooks, and stores | **501 / 501 PASS** (48 files) | 3.90s | `artifacts/v17_5_5/logs/gate8_vitest.log` |
| **Gate 9** | Frontend Build | `tsc -b && vite build` | **PASS (0 errors)** | 5.27s | `artifacts/v17_5_5/logs/gate9_build.log` |
| **Gate 10** | Frontend Lint | `oxlint` | **PASS (0 errors)** | 0.30s | `artifacts/v17_5_5/logs/gate10_lint.log` |
| **Gate 11** | Playwright Browser E2E | `tests/e2e/v17_5_5_chart_evidence.e2e.ts` (canvas diagnostics, geometry, rapid selection, mobile) | **4 / 4 PASS** | 10.69s | `artifacts/v17_5_5/logs/gate11_playwright.log` |

---

## 3. Four Closures Verification Details

### Closure 1: Entry Context Gate
- **Problem Solved:** Previously, `advance_stop_comparison` could transition variants to `OPEN` even when `context_status` was unverified or `cost_snapshot` was missing/invalid.
- **Implementation:**
  - Added `ComparisonEntryGateResult` dataclass to `stop_placement_types.py`.
  - Implemented `evaluate_stop_comparison_entry_gate` in `stop_placement_research_service.py`.
  - Wired into `advance_stop_comparison` under `WAIT_ENTRY` state: blocks entry with `CONTEXT_INCOMPLETE`, `COST_UNAVAILABLE`, `INVALID_COST`, `EXPIRED`, or `NOT_ELIGIBLE`.
  - Open variants bypass the entry gate and evaluate exits without obstruction (`test_open_variant_not_blocked_from_evaluating_exits`).
- **Tests:** `backend/tests/test_v17_5_5_entry_context_gate.py` (10/10 PASS).

### Closure 2: Shared Cost Authority
- **Problem Solved:** Research orders used arbitrary fees or `.0006` slippage discrepancy compared to production orders.
- **Implementation:**
  - Implemented `resolve_execution_cost_context` in `plan_financial_context_service.py`.
  - Standardized rates: maker fee `0.0002` (0.02%), taker fee `0.0004` (0.04%).
  - Validates non-negative finite values (`math.isfinite(v) and v >= 0.0`), distinguishing explicit `0.0` slippage from missing snapshots.
  - Returns `available=False, reason="MISSING_COST_SNAPSHOT"` when snapshot, candidate, settings, and config are all absent.
  - Wired into `read_verified_comparison_cost_snapshot` and `resolve_stop_comparison_execution_context`.
- **Tests:** `backend/tests/test_v17_5_5_cost_authority.py` (6/6 PASS).

### Closure 3: Protected Origin Binding Bridge
- **Problem Solved:** Break event descriptors were built without structured verification of swing origin timing relative to break occurrences and decision cutoffs.
- **Implementation:**
  - Implemented `select_protected_origin_for_break` in `stop_placement_service.py` to validate swing origins against break bar time, closed bar extremes, and tick tolerance.
  - Updated `build_selected_break_descriptor` to receive `origin_pivot_id`, `episode_start_ms`, `origin_price`, and `origin_occurred_at_ms`.
  - Updated `ScalpM1ModelService` (`evaluate_m1_continuation_candidate` and `evaluate_m1_fvg_continuation_candidate`) to call `select_protected_origin_for_break` and pass `origin_proof` into break descriptor and `tick_size` into `resolve_stop_anchor`.
- **Tests:** `backend/tests/test_v17_5_5_origin_bridge.py` (5/5 PASS).

### Closure 4: Real Chart Evidence & Overlay Geometry
- **Problem Solved:** Visual verification previously relied on DOM element presence without validating that lightweight-charts attached primitives and projected correct coordinates.
- **Implementation:**
  - Added read-only `window.__AURUM_CHART_OVERLAY_DIAGNOSTICS__()` hook in `ChartComponent.tsx`.
  - Created Playwright browser test suite `tests/e2e/v17_5_5_chart_evidence.e2e.ts`.
  - Validated inverted canvas coordinate projection:
    - SHORT: `takeProfit < plannedEntry < stopLoss` => `tpY > entryY > slY`.
    - LONG: `stopLoss < plannedEntry < takeProfit` => `slY > entryY > tpY`.
  - Validated rapid selection switching cleanly detaches prior primitive and attaches the latest primitive without leaking coordinates.
  - Validated mobile viewport (`390x844`) renders without horizontal scroll overflow.
- **Tests:** `frontend/tests/e2e/v17_5_5_chart_evidence.e2e.ts` (4/4 PASS).

---

## 4. Key File Hashes & Provenance

```json
{
  "backend/services/stop_placement_types.py": "a86ff2e3b2c855a6",
  "backend/services/plan_financial_context_service.py": "1114e1e05f428592",
  "backend/services/stop_placement_service.py": "a6f1b2ff23497caa",
  "backend/services/stop_placement_research_service.py": "106192af2ab64dae",
  "backend/services/scalp_m1_model_service.py": "23c8fdc68829312b",
  "backend/services/stop_placement_policy_service.py": "98954b73f35f264d",
  "backend/services/scalp_watcher_orchestrator.py": "588d938e50e0d86e",
  "backend/services/execution_consumer.py": "a6d4cff45268431b",
  "backend/services/cg01_engine.py": "060d27fc80a5fb40",
  "backend/services/post_trade_review_service.py": "691378f5f4267c8b",
  "backend/services/order_plan_snapshot_service.py": "9c58c885858beacc",
  "frontend/src/ChartComponent.tsx": "2eddb5ef58b931ab",
  "backend/tests/test_v17_5_5_entry_context_gate.py": "3aadbad7112fa71b",
  "backend/tests/test_v17_5_5_cost_authority.py": "9fced8484c1290f2",
  "backend/tests/test_v17_5_5_origin_bridge.py": "8d3a94a0f94187a2",
  "backend/tests/test_v17_5_5_cycle_to_chart_contract.py": "e618070828ab9be2",
  "frontend/tests/e2e/v17_5_5_chart_evidence.e2e.ts": "0abcc3743fec9ad0",
  "tools/run_v17_5_5_acceptance.py": "authoritative"
}
```

---

## 5. Limitations & Empirical Caveat

> [!WARNING]
> **Empirical Performance Caveat (`EMPIRICAL_UNPROVEN`):**
> Architectural and technical correctness of the entry gate, cost authority, origin binding, and chart rendering is verified across 155 backend unit/integration tests and 501 frontend tests. However, the empirical superiority of Variant C (Structural Dynamic SL) over Variant A (Legacy Fixed SL) and Variant B (Anchor + Ticks SL) remains `EMPIRICAL_UNPROVEN` until at least 30 closed forward paper trades accumulate under live market conditions.

---

## 6. Verdict: ACCEPTED

All 11 technical gates passed cleanly. The codebase is ready for production commit and remote push to branch `fix/v14-1-rr-flow`.
