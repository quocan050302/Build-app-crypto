# AURUM V17.5.4 — FINAL ACCEPTANCE REPORT

**Branch:** `fix/v14-1-rr-flow`  
**Base Commit:** `d43d3cb5c4593bc05dc291131a320e3cd70c49cd` (`fix(v17.5.3): verify structural origins and executable research lifecycle`)  
**Target Commit:** `fix(v17.5.4): bind production research context and verify stop evidence`  
**Execution Timestamp:** 2026-10-24T06:44:32Z  
**Technical Status:** `IMPLEMENTATION_VERIFIED`  
**Empirical Status:** `EMPIRICAL_UNPROVEN`  

---

## 1. Executive Summary

AURUM V17.5.4 delivers the final patch addressing production context binding, cost & slippage parity, structural stop origin proof, dynamic cycle reconciliation without hardcoded outcomes, and verified real browser tests.

All ten technical gates were executed under authoritative runner `tools/run_v17_5_4_acceptance.py`. Every test count was dynamically parsed from JUnit XML and test runners without hardcoded values.

- **Backend Tests:** 132 / 132 PASSED across 6 technical gates.
- **Frontend Vitest:** 501 / 501 PASSED across 48 test files.
- **Frontend Build & Lint:** PASSED with 0 errors.
- **Playwright Browser E2E:** 4 / 4 PASSED with real seed, API, geometry, and UI drawer assertions.
- **Verdict:** **ACCEPTED**

---

## 2. Technical Gate Verification Matrix

| Gate | Description | Test Targets | Result | Duration | Log File |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Gate 1** | V17.5.4 Four Core Suites | `test_v17_5_4_context_binding.py`<br>`test_v17_5_4_cost_parity.py`<br>`test_v17_5_4_anchor_proof.py`<br>`test_v17_5_4_cycle_reconciliation.py` | **16 / 16 PASS** | 1.49s | `artifacts/v17_5_4/logs/gate1_v17_5_4_core.log` |
| **Gate 2** | V17.5.3 Probes & Cycle Review | `test_v17_5_3_boundary_probes.py`<br>`test_v17_5_3_cycle_to_review.py` | **17 / 17 PASS** | 1.40s | `artifacts/v17_5_4/logs/gate2_v17_5_3.log` |
| **Gate 3** | V17.5.2 Baseline Suites | `test_v17_5_2_boundary_probes.py`<br>`test_v17_5_2_detector_to_exit.py` | **7 / 7 PASS** | 1.27s | `artifacts/v17_5_4/logs/gate3_v17_5_2.log` |
| **Gate 4** | V17.5.1 Feature Suites & Probes | 9 feature test suites (`red_probes`, `anchor_leg`, `structural_gate`, `atr_timeframe`, `policy_cas`, `research_runtime`, `production_flow`, `guard_matrix`, `stop_costs`) | **26 / 26 PASS** | 1.82s | `artifacts/v17_5_4/logs/gate4_v17_5_1.log` |
| **Gate 5** | Existing V17.5 Suites | 9 stop placement suites (`stop_placement`, `anchor_binding`, `risk_invariants`, `policy_api`, `exit_evidence`, `model_integration`, `production_flow`, `stop_review`, `stop_research`) | **31 / 31 PASS** | 1.30s | `artifacts/v17_5_4/logs/gate5_v17_5.log` |
| **Gate 6** | Prior Regressions | CG01, sessions roundtrip, atomic mirrors, baseline probes, noop authority | **35 / 35 PASS** | 3.84s | `artifacts/v17_5_4/logs/gate6_prior.log` |
| **Gate 7** | Frontend Vitest Suites | Unit and contract suites across all components, hooks, and stores | **501 / 501 PASS** (48 files) | 3.90s | `artifacts/v17_5_4/logs/gate7_vitest.log` |
| **Gate 8** | Frontend Build | `tsc -b && vite build` | **PASS (0 errors)** | 5.80s | `artifacts/v17_5_4/logs/gate8_build.log` |
| **Gate 9** | Frontend Lint | `oxlint` | **PASS (0 errors)** | 0.34s | `artifacts/v17_5_4/logs/gate9_lint.log` |
| **Gate 10** | Playwright Browser E2E | `tests/e2e/v17_5_4_stop_browser.e2e.ts` (real seed, API, geometry, drawer) | **4 / 4 PASS** | 9.41s | `artifacts/v17_5_4/logs/gate10_playwright.log` |

---

## 3. Detailed Verification Findings

### A. Context Binding (Gate 1 — `test_v17_5_4_context_binding.py`)
- Verified that `resolve_stop_comparison_execution_context` extracts production candidate attributes: `order_type`, `entry_intent`, absolute `expires_at_ms`, `decision_cutoff_ms`, `cost_snapshot`, `provenance`, and sets `context_status="CONTEXT_VERIFIED"`.
- Verified that `bind_stop_comparison_execution_context` immutably binds this context into `cand.stop_comparison`.
- Verified `evaluate_cycle` in `ScalpWatcherOrchestrator` invokes `resolve_stop_comparison_execution_context` using `policy_dict` and `session_ctx`, ensuring context is bound before calling `persist_stop_comparison`.
- Incomplete context paths correctly receive status `CONTEXT_INCOMPLETE` with explicit `context_reason`.

### B. Cost & Slippage Parity (Gate 1 — `test_v17_5_4_cost_parity.py`)
- Verified `read_verified_comparison_cost_snapshot` extracts `entry_slippage_usd`, `exit_slippage_usd`, and `fee_usd` from frozen snapshot with safe fallback.
- In `advance_stop_comparison`:
  - Entry trigger passes `order_slippage = costs["entry_slippage_usd"]`.
  - Trade excursions (`trade_mae`, `trade_mfe`) are isolated strictly to variants in `OPEN` state referencing `actual_entry`.
  - Exit evaluation passes full `cost_snapshot` into `evaluate_order_exit`.

### C. Structural Anchor Proof (Gate 1 — `test_v17_5_4_anchor_proof.py`)
- `resolve_bound_stop_leg` enforces four strict proof barriers:
  1. Source candle exists in `closed_bars` (exact timestamp match).
  2. Pivot price matches candle extreme within `tolerance_tick or tick_size` (derived from instrument metadata, not hardcoded 0.05).
  3. Pivot origin belongs to displacement episode of selected break (prioritizes `target_origin_id` from `selected_break`).
  4. Strict close-through check: SHORT (`b_close < ref_px`), LONG (`b_close > ref_px`).
- Preserves `bound_leg` into `StopAnchorResult(status="AVAILABLE")` and `StopPlacementDecision`.

### D. Full Cycle Reconciliation (Gate 1 — `test_v17_5_4_cycle_reconciliation.py`)
- `build_trade_outcome_from_order` dynamically computes `gross_pnl`, `net_pnl`, `realized_r` from order accounting without hardcoded values.
- `build_post_trade_review` dynamically calculates gross PnL from `(exit - entry) * qty * dir_mult` when missing.
- Replaced legacy hardcoded `gross_pnl=5.0` in `test_v17_5_2_detector_to_exit.py`.

### E. Real Chart & Browser Verification (Gate 10 — `v17_5_4_stop_browser.e2e.ts`)
- Eliminated trivial `expect(pageContent).toBeTruthy()` assertions.
- Verified HTTP status 200 and response body on `/__e2e/seed_order`.
- Verified GET `/api/v1/orders/{order_id}/stop-evidence` returns `status="AVAILABLE"`.
- Verified strict geometry:
  - SHORT: `take_profit < actual_entry < actual_sl` (2625.0 < 2650.0 < 2660.0).
  - LONG: `actual_sl < actual_entry < take_profit` (2640.0 < 2650.0 < 2675.0).
- Navigated to Journal tab in real browser, opened trade details drawer, switched to Tab B ("B. Giả Thuyết & SMC"), and verified `StopPlacementDetails` displays actual SL, structural anchor description, and verified badge `Xác minh cấu trúc (C)`.
- Verified legacy order returns `status="UNAVAILABLE_LEGACY"` and displays `SL cố định legacy`.
- Verified mobile viewport (390x844) renders without horizontal scroll overflow.

---

## 4. Key File Hashes & Provenance

```json
{
  "backend/services/stop_placement_types.py": "172df6369e42215d",
  "backend/services/stop_placement_service.py": "6baae30a76a76c58",
  "backend/services/stop_placement_policy_service.py": "98954b73f35f264d",
  "backend/services/stop_placement_research_service.py": "78af2fe312c8000b",
  "backend/services/scalp_m1_model_service.py": "40f0e05581cb38df",
  "backend/services/scalp_watcher_orchestrator.py": "588d938e50e0d86e",
  "backend/services/post_trade_review_service.py": "691378f5f4267c8b",
  "frontend/src/components/StopPlacementDetails.tsx": "6e341c8882e109d2",
  "backend/tests/test_v17_5_4_context_binding.py": "c570da3245065561",
  "backend/tests/test_v17_5_4_cost_parity.py": "5d26654b38324e7d",
  "backend/tests/test_v17_5_4_anchor_proof.py": "93adda56036f2a9f",
  "backend/tests/test_v17_5_4_cycle_reconciliation.py": "0ca5cf16073d9371",
  "frontend/tests/e2e/v17_5_4_stop_browser.e2e.ts": "792b846a8eca3a6f",
  "tools/run_v17_5_4_acceptance.py": "computed dynamically in acceptance.json"
}
```

---

## 5. Limitations & Caveats

1. **Empirical Performance Status:** `EMPIRICAL_UNPROVEN`. Under the system's empirical governance invariant, structural stop placement cannot be declared statistically superior to fixed stops until at least 30 live closed trades have been executed and reviewed in production.
2. **Research Execution Model:** Research variants B and C evaluate concurrently in shadow mode with frozen cost snapshot parity, serving as an empirical comparison control against Variant A.
3. **Anchor Proof Constraints:** Structural stops fail closed when the displacement origin candle cannot be proven in `closed_bars` or when price deviates beyond instrument tick size.
