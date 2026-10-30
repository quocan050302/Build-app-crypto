# AURUM DESK V17.8.0.2 — FULL REGRESSION MATRIX (PHASES B & E)

## 1. Suite Execution Summary (`FINAL_CODE_SHA = 68ec35261e7544c51bb08bf8f5360e4031921b17`)

| Suite / Gate | Command / Scope | Collected | Passed | Failed | Skipped | Errors | Duration | Status |
|---|---|---:|---:|---:|---:|---:|---:|---|
| **V17.8 Runtime Integration** | `pytest backend/tests/test_v17_8_runtime_integration.py` | 10 | 10 | 0 | 0 | 0 | 8.93s | **PASS** |
| **V17.8.0.2 Observe-Only Runtime** | `pytest backend/tests/test_v17_8_0_2_observe_only_runtime.py` | 4 | 4 | 0 | 0 | 0 | 7.05s | **PASS** |
| **V17.8 Acceptance Runner Unit Tests** | `pytest backend/tests/test_v17_8_acceptance_runner.py` | 7 | 7 | 0 | 0 | 0 | 6.42s | **PASS** |
| **V17.7 Database Safety (`G02`–`G04`)** | `pytest backend/tests/test_v17_7_database_safety.py` | 30 | 27 | 0 | 3 | 0 | 10.18s | **PASS** |
| **V17.7 Adaptive Stop Loss (`G17`)** | `pytest backend/tests/test_v17_7_adaptive_stop_loss.py` | 102 | 102 | 0 | 0 | 0 | 18.02s | **PASS** |
| **V17.5 Regression (`G18`)** | `pytest backend/tests/test_v17_5*.py` (48 files) | 278 | 278 | 0 | 0 | 0 | 43.98s | **PASS** |
| **V17.6 Regression (`G19`)** | `pytest backend/tests/test_v17_6*.py` (76 files) | 776 | 776 | 0 | 0 | 0 | 514.01s | **PASS** |
| **Full Backend Pytest (`G20`)** | `pytest backend/tests` | 4171 | 4163 | 0 | 8 | 0 | 1335.71s | **PASS** |
| **Frontend Vitest (`G21`)** | `npm test -- --run` (57 files) | 552 | 552 | 0 | 0 | 0 | 8.04s | **PASS** |
| **Frontend Build (`G22`)** | `npm run build` (`tsc -b && vite build`) | — | 1 | 0 | 0 | 0 | 8.68s | **PASS** |
| **Frontend Oxlint (`G23`)** | `npx oxlint` (171 files) | — | 1 | 0 | 0 | 0 | 1.26s | **PASS** |
| **Playwright Stop Browser E2E (`G24`)** | `npx playwright test tests/e2e/v17_5_3_stop_browser.e2e.ts` | 3 | 3 | 0 | 0 | 0 | 14.88s | **PASS** |
| **Playwright SL/TP Intelligence E2E (`G50`)** | `npx playwright test tests/e2e/v17_8_0_1_sltp_intelligence.e2e.ts` | 3 | 3 | 0 | 0 | 0 | 15.50s | **PASS** |

## 2. Critical Scenario Coverage (25/25 Verified)
All 25 mandatory scenarios from Section 5.2 (Legacy parity, Shadow compare, Paper Structural V17.7 fallback, Paper Structural + V17.8 Bullish/Bearish Unicorn, ordinary FVG without breaker, unclosed HTF rejection, consumed FVG rejection, stale/inverted quote rejection, pre-ARM & pre-FILL stop breach, pre-FILL protective distance erosion, >4x/10x ATR valid structural SL with downscaled quantity, wide SL net R:R fail-closed, missing required M1 ATR fail-closed, no SL widening after ARM/FILL, no quantity increase at FILL when equity rises, SHORT Ask stop trigger, LONG Bid stop trigger, policy persistence across reload, CAS conflict rejection, LIVE scope rejection, honest replay status, and Variant D calibration disclosure) are covered and passing in `backend/tests/test_v17_8_runtime_integration.py`.
