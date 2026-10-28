# AURUM DESK V17.7.2 — FULL REGRESSION REPORT
**Release:** V17.7.2  
**Date:** October 2026  
**Environment:** Python 3.13.0, SQLite (Isolated Temp DB), Node.js, Vite 8, Vitest 5, Playwright 1.64  
**Status:** ALL GATES PASSED (ZERO REGRESSIONS)  

---

## 1. Regression Test Execution Summary

Every regression test suite was executed against the active codebase on branch `feat/v17-7-adaptive-structural-stop-loss`.

| Test Suite | Scope / Files | Collected | Passed | Failed | Skipped | Status | Duration |
|---|---|---|---|---|---|---|---|
| **V17.7.2 Dedicated Suite** | `test_v17_7_adaptive_stop_loss.py` (SL-01..60, DSL-01..20, CE-01..20) | 100 | 100 | 0 | 0 | **PASS** | 6.31s |
| **V17.5 Regression Suite** | `backend/tests/test_v17_5*.py` | 278 | 278 | 0 | 0 | **PASS** | 10.35s |
| **V17.6 Regression Suite** | `backend/tests/test_v17_6*.py` (incl. V17.6.4.3d) | 776 | 776 | 0 | 0 | **PASS** | 63.42s |
| **Frontend Vitest Suite** | `frontend/src/**/*.{test,spec}.{ts,tsx}` | 548 | 548 | 0 | 0 | **PASS** | 9.22s |
| **Frontend Build & Lint** | `npm run build && npx oxlint` | 168 files | 168 files | 0 errors | 0 | **PASS** | 9.80s |
| **Playwright E2E Suite** | `frontend/tests/e2e/v17_5_3_stop_browser.e2e.ts` | 3 | 3 | 0 | 0 | **PASS** | 23.00s |

**Total Verified Tests Across All Suites:** 1,705 automated tests passed with zero failures.

---

## 2. Key Regression Invariants Verified

### 2.1 Preserved V17.5 Structural Governance
- Protected leg low and high anchor resolution preserved.
- Temporal cutoff verification preserved.
- Invalidation breach detection preserved.
- Excursion tracking (MAE/MFE) preserved.

### 2.2 Preserved V17.6.4.3d Concurrency & Feed Hardening
- Unique commit truth and rollback semantics preserved.
- Database isolation guards verified across independent sessions.
- Discovery integrity and observation recording preserved.
- Absence of background loop interference during test runs verified.

### 2.3 Preserved Risk & Financial Constraints
- No Stop Loss widening post-ARM permitted.
- Fill-time quantity preservation/downsizing (`final_quantity <= armed_quantity`) verified.
- Exact instrument tick quantization preserved.
- Zero live order network calls verified.

### 2.4 Browser E2E Rendering Integrity
- Real Google Chrome browser successfully launched via Playwright webServer.
- SHORT and LONG stop loss / target price geometry correctly rendered on lightweight-charts.
- Mobile viewport responsive layout verified without horizontal overflow.
