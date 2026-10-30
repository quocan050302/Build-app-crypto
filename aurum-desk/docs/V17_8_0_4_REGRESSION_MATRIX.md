# AURUM DESK V17.8.0.4 — FULL REGRESSION & JUNIT RECONCILIATION MATRIX

## 1. Executive Summary
- **Final Code SHA:** `c356d6551e13cd6af61cea5c3ab1f4aae1548af2`
- **Acceptance Run ID:** `run_v17_8_0_4_1793248273004_25296`
- **Mandatory Gates Passed:** `88 / 88` (`0` failed, `0` blocked)
- **JUnit XML Files Reconciled (`G87`):** `75` XML files (`5510 / 5520` cumulative gate testcase assertions passed, `10` skipped, `0` failed, `0` errors, `0` mismatches)

## 2. Suite-by-Suite Regression Breakdown (Actual Executed Counts)
| Suite / Gate | Collected | Passed | Failed | Skipped | Errors | Status |
|---|---:|---:|---:|---:|---:|---|
| **Full Backend Pytest (`G20_FULL_BACKEND_PYTEST` — Standalone Suite Count)** | `4194` | `4186` | `0` | `8` | `0` | `PASS` |
| **V17.5 Regression (`G18_V17_5_REGRESSION`)** | `278` | `278` | `0` | `0` | `0` | `PASS` |
| **V17.6 Regression (`G19_V17_6_REGRESSION`)** | `776` | `776` | `0` | `0` | `0` | `PASS` |
| **Cumulative JUnit XML Gate Executions (`G87` Across All `75` Gate XMLs)** | `5520` | `5510` | `0` | `10` | `0` | `PASS` |
| **Frontend Vitest (`G21_FRONTEND_VITEST`)** | `554` | `554` | `0` | `0` | `0` | `PASS` |
| **Frontend Production Build (`G22_FRONTEND_BUILD`)** | `1` | `1` | `0` | `0` | `0` | `PASS` |
| **Frontend Oxlint (`G23_FRONTEND_LINT`)** | `1` | `1` | `0` | `0` | `0` | `PASS` |
| **Playwright Stop Browser E2E (`G24_PLAYWRIGHT_BROWSER_E2E`)** | `3` | `3` | `0` | `0` | `0` | `PASS` |
| **Playwright SL/TP Intelligence E2E (`G50_FRONTEND_MOUNTED_SLTP_INTELLIGENCE_VIEW_E2E`)** | `5` | `5` | `0` | `0` | `0` | `PASS` |
| **Frontend Shadow Observation E2E (`G72_FRONTEND_SHADOW_OBSERVATION_E2E`)** | `11` | `11` | `0` | `0` | `0` | `PASS` |
