# AURUM DESK V17.7.4.1 — FULL REGRESSION TEST REPORT

**Release Version:** V17.7.4.1  
**Target Branch:** `feat/v17-7-adaptive-structural-stop-loss`  
**Execution Date:** 2026-10-27  
**Execution Environment:** macOS, Python 3.13 (Virtualenv), Node.js v20+, Vitest v5.0.3, Oxlint v1.81.0, Playwright v1.64.0  
**Overall Regression Verdict:** PASS (100% Zero Failures)  

---

## 1. Executive Summary

This report documents the exhaustive multi-tier regression execution for Aurum Desk V17.7.4.1 across backend, frontend, unit, integration, browser E2E, and release acceptance test suites.

Every suite was executed in fresh, isolated environments with strict test database root containment (`TESTING=1`, `AURUM_TEST_RUN_ID` enforcement, and hardlink alias detection).

---

## 2. Test Execution Matrix by Suite

| Suite Name | Execution Command | Scope | Result | Passing / Total | Execution Time |
|---|---|---|---|---|---|
| **Targeted V17.7 Adaptive SL Suite** | `pytest backend/tests/test_v17_7_adaptive_stop_loss.py` | Adaptive Stop Loss, SMC anchors, distant SL, ARM, exits | **PASS** | 72 / 72 | 8.2s |
| **ATR Provenance & Authority** | `pytest backend/tests/test_v17_7_atr_provenance.py` | Causal ATR, fail-closed, multi-timeframe, ARM boundary | **PASS** | 69 / 69 | 7.3s |
| **Database Isolation & Safety** | `pytest backend/tests/test_v17_7_database_safety.py` | Marker checks, hardlink alias, pre-existing files, sentinels | **PASS** | 30 / 30 | 5.1s |
| **Full Backend Pytest Regression** | `pytest backend/tests -q` | Complete 424 test files across all backend services | **PASS** | 4,128 / 4,128 | 428s |
| **Frontend Vitest Unit Suite** | `npm test -- --run` (in `frontend/`) | React 19 components, hooks, chart annotations, UI | **PASS** | 548 / 548 (56 files) | 4.77s |
| **Frontend Production Build** | `npm run build` (in `frontend/`) | TypeScript type check (`tsc -b`) & Vite bundling | **PASS** | Built in 1.18s | 1.18s |
| **Frontend Static Analysis** | `npx oxlint` (in `frontend/`) | 168 files, 116 rules | **PASS** | 0 errors | 0.30s |
| **Playwright Browser E2E** | `playwright test tests/e2e/v17_5_3_stop_browser.e2e.ts` | Headless browser, geometry, drawer rendering | **PASS** | 4 / 4 | 7.8s |
| **Zero Live Orders Safety Boundary** | Runner spy + `test_sl_50`, `test_dsl_19`, `test_ce_18` | Interceptor boundary & negative controls | **PASS** | 3 / 3 (0 sent) | 2.1s |

---

## 3. Targeted V17.7 Regression Analysis

### 3.1 Adaptive Stop Loss Invariants (`test_v17_7_adaptive_stop_loss.py`)
- **Anchor Causality:** Swing high/low, MSS, and FVG anchors are strictly causal and immutable once formed.
- **Directional Geometry:**
  - `LONG`: $\text{SL} < \text{Entry} < \text{TP}$
  - `SHORT`: $\text{TP} < \text{Entry} < \text{SL}$
- **Distant Stop Loss (>4x ATR):**
  - Demonstrated valid LONG with SL at 6x ATR and valid SHORT with SL at 7x ATR.
  - Sizing appropriately downscales position contracts to respect risk budget ($R \le 12.50$ USDT).
  - Net R:R $\ge 1.30$ strictly maintained.

### 3.2 ATR Authority Repairs (`test_v17_7_atr_provenance.py`)
- Confirmed `ATR-FINAL-01` through `ATR-FINAL-15` pass.
- Missing counts, non-numeric cutoffs, negative timestamps, and stale ATR strictly fail closed.
- Optional M5 ATR does not alter or contaminate primary buffer when malformed.
- Weighted buffer cannot impersonate raw ATR in distance metrics.

### 3.3 Database Safety Repairs (`test_v17_7_database_safety.py`)
- Confirmed `DB-FINAL-01` through `DB-FINAL-12` pass.
- Hardlinked files to external databases (`st_nlink > 1`) fail with `HARDLINK_ALIAS_DETECTED`.
- Pre-existing files in test-owned directories fail with `PRE_EXISTING_EXTERNAL_FILE_REJECTED`.
- Corrupted or foreign-run markers fail closed with typed exceptions.

---

## 4. Zero Live-Order Dispatch Verification

During the entire regression execution:
- Outbound exchange adapters remained blocked or unconfigured.
- Simulated broker recorded:
  ```text
  LIVE_ORDER_DISPATCH_ATTEMPTS: 0
  LIVE_ORDER_DISPATCH_SUCCESSES: 0
  ```
- Negative control in `verify_live_order_safety_boundary()` confirmed that an attempted live order submission immediately raises `AssertionError: CRITICAL SAFETY VIOLATION: Live order dispatch detected!`.

---

## 5. Conclusion

The full regression suite demonstrates that Aurum Desk V17.7.4.1 has resolved all residual safety defects without introducing regressions into existing SMC/ICT strategies, risk models, database durability, or frontend components.
