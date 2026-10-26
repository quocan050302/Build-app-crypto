# AURUM DESK V17.7.2 — DEFECT CLOSURE REGISTER
**Release:** V17.7.2  
**Focus:** Complete Traceability of Confirmed Defects, Root Causes, Implemented Fixes & Verifying Gates  

---

## 1. Defect Closure Matrix

| Defect ID | Severity | File & Component | Root Cause | Implemented Fix | Verification Gate / Test | Status |
|---|---|---|---|---|---|---|
| **V172-P0-01** | CRITICAL | `backend/services/stop_placement_service.py` (`plan_structural_stop`) | Missing/invalid M1 ATR only recorded diagnostic classification `DISTANCE_ATR_UNAVAILABLE` while continuing to compute valid stop. | Added Step 8 fail-closed barrier: returns `StopStatus.WAITING_VOLATILITY_EVIDENCE`, `selected_sl = None`, `applied_variant = "NONE"`. | `CE-01`, `CE-02`, `CE-03`, `DSL-10` | **CLOSED** |
| **V172-P0-02** | CRITICAL | `tools/run_v17_7_acceptance.py` | Acceptance runner skipped mandatory gates (Playwright, full pytest) while still declaring acceptance. | Rebuilt runner to execute all 23 release gates (G01..G23) fail-closed; skips or unverified gates block release. | `CE-19`, Acceptance Runner Execution | **CLOSED** |
| **V172-P0-03** | CRITICAL | Safety Test Suite & Outbound Boundary | Safety test patched HTTP but did not execute actual candidate/ARM/fill/exit execution flow. | Installed active spy across full pipeline execution (candidate -> ARM -> fill -> close) + negative control injection. | `SL-50`, `DSL-19`, `CE-18` | **CLOSED** |
| **V172-P0-04** | CRITICAL | `backend/database.py` & Test Fixtures | Fixture imported shared engine; when configured with `AURUM_DB_PATH`, test cleanups executed destructive SQL against runtime DB. | Enforced test DB isolation before engine binding; implemented fail-closed guard `assert_test_database_isolated()`. | `CE-17`, Test Suite Teardown Guard | **CLOSED** |
| **V172-P1-01** | HIGH | `backend/services/cg01_engine.py` | Tests invoked isolated helper functions without executing production `evaluate_cg01_candidate()`. | Updated tests to invoke real `evaluate_cg01_candidate()` with canonical closed candle bundle and live quote. | `SL-36`, `CE-11` | **CLOSED** |
| **V172-P1-02** | HIGH | `backend/services/scalp_watcher_orchestrator.py` | End-to-end tests manually created `PaperOrder(state="armed")`, bypassing the real ARM boundary. | Updated tests to invoke real production `ScalpWatcherOrchestrator._arm_paper_candidate()`. | `SL-43`, `SL-60`, `CE-13` | **CLOSED** |
| **V172-P1-03** | HIGH | Concurrency Suite | Tests inserted duplicate orders sequentially rather than running true concurrent races. | Implemented multi-threaded race synchronized via `threading.Barrier(2)` across independent sessions. | `SL-57`, `DSL-18`, `CE-16` | **CLOSED** |
| **V172-P1-04** | HIGH | Acceptance Artifact Provenance | Artifact recorded stale commit SHA (`6b4a62...`) conflicting with actual branch HEAD (`9aaaa5...`). | Automated exact git commit SHA extraction from `git rev-parse HEAD` and synchronized artifact. | `CE-20`, Acceptance Runner G23 | **CLOSED** |
| **V172-P1-05** | HIGH | `backend/services/trade_lifecycle_service.py` | Fill execution took raw calculator quantity, allowing quantity to expand beyond frozen armed size. | Enforced `final_quantity = min(calc_result.quantity, order.quantity)` in `execute_fill()`. | `SL-46`, `CE-14` | **CLOSED** |
| **V172-P1-06** | HIGH | `cg01_context_adapter.py` & `stop_placement_service.py` | Spread calculation clipped negative spread via `max(0.0, ask - bid)`, masking inverted quotes. | Removed clipping, preserved raw spread, strictly rejected inverted quotes (`ask < bid`) with `INVERTED_QUOTE`. | `CE-09`, `CE-10` | **CLOSED** |
| **V172-P1-07** | HIGH | Playwright E2E & Browser Coverage | Playwright browser E2E was skipped in runner and marked as verified without browser launch. | Executed actual Playwright suite (`v17_5_3_stop_browser.e2e.ts`) using automated webServer with Chrome. | `G20`, Playwright List & JSON Reporter | **CLOSED** |
| **V172-P1-08** | MEDIUM | Release Documentation | Documentation claimed acceptance without empirical machine metrics. | Replaced placeholder claims with exact test counts, execution durations, and authentic artifacts. | `G22`, Release Documentation Suite | **CLOSED** |

---

## 2. Closure Authorization

All 12 defects (4 Critical, 7 High, 1 Medium) are verifiably closed by active code implementation and automated test regression.
Zero unresolved defects remain.
