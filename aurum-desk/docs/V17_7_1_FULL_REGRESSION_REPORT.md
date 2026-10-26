# AURUM DESK V17.7.1 — FULL REGRESSION REPORT
**Role:** Senior Release Engineer & QA Systems Architect  
**Date:** October 2026  
**Branch:** `feat/v17-7-adaptive-structural-stop-loss`  
**Version:** V17.7.1  

---

## 1. Regression Matrix Overview

The V17.7.1 release was validated across the complete backend and frontend regression matrix to guarantee zero regressions against prior trading logic, SMC/ICT structure detection, risk calculation, order lifecycles, and user interface contracts.

| Test Suite / Target | Command | Execution Time | Collected | Passed | Failed | Status |
|---|---|---|---|---|---|---|
| **V17.7.1 Dedicated Suite** | `pytest backend/tests/test_v17_7_adaptive_stop_loss.py -v` | 0.93s | 80 | 80 | 0 | **PASS** |
| **V17.5 Regression Suite** | `pytest backend/tests/test_v17_5*.py -q` | 6.84s | 278 | 278 | 0 | **PASS** |
| **V17.6 Regression Suite** | `pytest backend/tests/test_v17_6*.py -q` | 40.44s | 776 | 776 | 0 | **PASS** |
| **Frontend Vitest Suite** | `npm test -- --run` (in `frontend/`) | 5.17s | 548 | 548 | 0 | **PASS** |
| **Frontend Production Build** | `npm run build` (in `frontend/`) | 6.80s | — | — | 0 | **PASS** |
| **Frontend Oxlint** | `npx oxlint` (in `frontend/`) | 0.20s | 168 files | 0 errors | 0 | **PASS** |
| **Live Order Safety Boundary** | Instrumented `httpx.Client.post` active spy | Instant | — | 0 calls | 0 | **PASS** |

---

## 2. Detailed Suite Results & Findings

### 2.1 Dedicated V17.7.1 Suite (`test_v17_7_adaptive_stop_loss.py`)
- **Gates:** 80 total gates (60 baseline gates `SL-01`..`SL-60` + 20 distant-SL gates `DSL-01`..`DSL-20`).
- **Pass Rate:** 100% (80/80 passed, 0 failures, 0 skipped).
- **Key Verifications:**
  - `SL-20`: Distant stops $> 4\times$ ATR accepted when structural evidence is valid and financial checks pass.
  - `SL-50`: Zero live exchange order submissions verified via real outbound HTTP client instrumentation.
  - `SL-60`: End-to-end persisted PAPER lifecycle (Candidate -> ARM -> Fill -> Close -> Evidence).
  - `DSL-01` to `DSL-20`: Comprehensive distant-SL invalidation, sizing, liquidation, determinism, and concurrency validation.

### 2.2 Prior V17.5 Regression Suite (`test_v17_5*.py`)
- **Files Covered:**
  - `test_v17_5_1_red_probes.py`
  - `test_v17_5_1_stop_contract.py`
  - `test_v17_5_model_integration.py`
  - `test_v17_5_policy_and_quantization.py`
  - `test_v17_5_research_and_metrics.py`
- **Result:** 278 passed in 6.84s (0 failures).
- **Regression Analysis:** Fixed an initial `AttributeError: 'Anchor' object has no attribute 'id'` by implementing safe `getattr(anchor, 'id', getattr(anchor, 'anchor_id', None))` in `plan_structural_stop`. All 278 V17.5 tests now pass without regression.

### 2.3 Prior V17.6 Regression Suite (`test_v17_6*.py`)
- **Files Covered:**
  - `test_v17_6_1_observation_dashboard.py`
  - `test_v17_6_2_events_broadcaster.py`
  - `test_v17_6_3_dst_midnight_boundary.py`
  - `test_v17_6_4_3b_unique_commit_truth.py`
  - `test_v17_6_4_3c_multi_source_dedup.py`
  - `test_v17_6_4_3d_regression_and_acceptance.py`
- **Result:** 776 passed in 40.44s (0 failures).
- **Regression Analysis:** Verifies complete transaction durability, unique commit truth, multi-source deduplication, observation event broadcasting, and DST midnight session boundaries.

### 2.4 Frontend Vitest Suite (`npm test -- --run`)
- **Files Covered:** 56 test files across `frontend/src/` (candidate scoring, CG01 realtime state, stop placement contract hooks, trade drawer components, R:R policy settings, tabs, and WebSocket hooks).
- **Result:** 548 passed in 5.17s (0 failures).

### 2.5 Frontend Build & Static Analysis
- **Vite Production Build:** Successfully compiled all assets, client chunks, and CSS bundle into `dist/` with zero TypeScript or bundling errors.
- **Oxlint:** Checked 168 files against 116 linting rules; completed in 202ms with 0 errors (64 non-blocking warnings).

---

## 3. Critical Safety Invariants Confirmation

1. **Zero Live Exchange Orders Sent:**
   - Active network spy attached to `httpx.Client.post` and `httpx.AsyncClient.post`.
   - Verified that `spy.call_count == 0` during all simulated order placement and candidate execution paths.
   - Verified that `PaperBroker` contains zero exchange order dispatch methods (`place_live_order`, `submit_exchange_order`, `send_order`).
   - Verified `LIVE_EXCHANGE_ORDERS_SENT == 0` from `services.execution_coordinator`.

2. **Database Isolation & Durability:**
   - All tests execute within isolated transactions or test databases.
   - Cleanup fixtures delete only test-prefixed records (`test_v17_7_%`), leaving persistent production data completely unmutated.

---

## 4. Final Regression Verdict

**ALL REGRESSION GATES PASSED.** Zero regressions introduced to existing functionality.
