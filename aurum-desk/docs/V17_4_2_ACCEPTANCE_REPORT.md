# AURUM V17.4.2 — Final Acceptance & Verification Report

**Implementation Status:** `IMPLEMENTED_AND_VERIFIED`  
**Base SHA:** `c112a4aa1c365ad1e78a047f8fca61517c4d96c5`  
**Branch:** `fix/v14-1-rr-flow`  
**Commit Message:** `fix(v17.4.2): revalidate execution quotes and close session writer bypasses`  
**Execution Scope:** PAPER ONLY (Authoritative database resolution; client scope overrides rejected)  
**Trading Invariants:** Max 3 fills/day (UTC+7 `Asia/Ho_Chi_Minh`), max 1 open position, max 1 armed order, zero NY preference/reservation, R:R floor 1.5 preserved.

---

## 1. Executive Summary

AURUM V17.4.2 implements complete, production-grade resolutions for the 4 identified defects (Q, N, L, C) across quote revalidation, no-op database concurrency authority, and session writer bypasses:

1. **Quote Revalidation at Execution Boundary (Deficiency Q):**  
   Removed `if isinstance(ticker, ValidatedQuote): return ticker` shortcut in `QuoteValidator.validate_ticker`. Pre-validated quotes are normalized without mutating the input object, preserving original provenance (`observed_at`, `exchange_time`) while strictly recomputing age against the caller's current `now_ms` and `max_age_sec`. A quote valid at T0 reused at T0+60s with `max_age_sec=15` is strictly rejected as stale (`is_valid=False, is_stale=True, freshness_sec >= 60.0`). Numerical checks (finite values, spread non-inversion) are strictly re-evaluated.
2. **Database Version Fence for No-op Concurrency (Deficiency N):**  
   Replaced cached ORM identity-map comparison in `apply_entry_session_update_plan` with an atomic SQL same-value version fence: `UPDATE TradingPolicy SET version=version WHERE id=1 AND version=expected`. Fresh column mappings are read directly from the database within the transaction boundary. Stale client drafts matching outdated local cache now encounter `fence_result.rowcount == 0` and are strictly rejected with HTTP 409 `StalePolicyVersionError`. True no-ops do not increment version, update timestamps, or emit audit event spam.
3. **Unified Server Execution Scope Gate (Deficiency L):**  
   Integrated `resolve_server_execution_scope` into `TradingPolicyService.update_policy`. Legacy writer requests attempting to activate `ALL_SESSIONS_SETUP_FIRST` on non-PAPER database environments are blocked with HTTP 400 before any database mutation.
4. **Normalized Final State Persistence across Writers (Deficiency C):**  
   In `main.update_scalp_config_endpoint`, eliminated the final goal write bug where stale local `ny_g` in `has_daily_update` overwrote `daily_ny_goal` to 1. Explicitly conflicting requests (`CG01_ALL_SESSIONS_PAPER` with `daily_ny_goal: 1`) are rejected with HTTP 400 `ENTRY_SESSION_CONFIG_CONFLICT`. Setup-first mode guarantees `eff_ny_g = 0` across all persistence layers.
5. **Runtime Status:** `CUTOVER_NOT_OBSERVED` (Development/test checkout environment). Ready for seamless user cutover via UI settings modal.

---

## 2. Baseline Red Probes vs. Post-Fix Verification

| Probe ID | Deficiency Description | Baseline Result (`c112a4a`) | V17.4.2 Result | Verification Proof |
|---|---|---|---|---|
| **Q** | `QuoteValidator.validate_ticker` returned pre-validated quotes directly without re-checking age | ❌ FAIL (`is_valid=True, freshness_sec=0.0` at T0+60s) | ✅ PASS (`is_valid=False, is_stale=True, freshness_sec=60.0`) | `test_v17_4_2_baseline_probes.py::test_prevalidated_quote_must_be_rechecked_at_execution_time` |
| **N** | `apply_entry_session_update_plan` no-op check used cached ORM entities before DB CAS | ❌ FAIL (Stale Session B returned 200 instead of 409) | ✅ PASS (Raises `StalePolicyVersionError`, HTTP 409) | `test_v17_4_2_baseline_probes.py::test_cached_noop_must_reject_stale_expected_version` |
| **L** | `POST /api/v1/policy/trading` bypassed server LIVE scope check | ❌ FAIL (Allowed `ALL_SESSIONS_SETUP_FIRST` on LIVE DB) | ✅ PASS (Rejected with HTTP 400 `ValueError`) | `test_v17_4_2_baseline_probes.py::test_legacy_policy_route_cannot_bypass_server_live_scope` |
| **C** | `POST /api/scalp/config` allowed conflicting timing + NY goal 1 | ❌ FAIL (Returned 200, persisted NY goal = 1 under setup-first) | ✅ PASS (Rejected with HTTP 400 `ENTRY_SESSION_CONFIG_CONFLICT`) | `test_v17_4_2_baseline_probes.py::test_legacy_scalp_route_rejects_conflicting_pair` |

---

## 3. Comprehensive Acceptance Gate Results

Executed via automated runner `tools/run_v17_4_2_acceptance.py`:

| Gate | Scope | Command | Tests Run | Result | Duration |
|---|---|---|---|---|---|
| **Gate 1** | Baseline Adversarial Probes (Q, N, L, C) | `pytest backend/tests/test_v17_4_2_baseline_probes.py` | 4 tests | **PASS** | 6.28s |
| **Gate 2** | Quote Revalidation & Execution Parity | `pytest backend/tests/test_v17_4_2_quote_revalidation.py backend/tests/test_v17_4_2_quote_execution.py` | 17 tests (Q01-Q09, QE01-QE08) | **PASS** | 16.83s |
| **Gate 3** | No-op Database Authority & Concurrency | `pytest backend/tests/test_v17_4_2_noop_authority.py` | 8 tests (N01-N08) | **PASS** | 14.70s |
| **Gate 4** | Session Writer Contracts & Scope Authority | `pytest backend/tests/test_v17_4_2_writer_contracts.py` | 12 tests (W01-W12) | **PASS** | 22.95s |
| **Gate 5** | V17.4.1 Full Regression Suite | `pytest backend/tests/test_v17_4_1_*.py` | 37 tests (CAS, flows, guards, scope, quota) | **PASS** | 63.65s |
| **Gate 6** | Selected Session & Startup Regressions | `pytest backend/tests/test_v14_4_*.py` | 32 tests (startup, 3-session entry/exec) | **PASS** | 5.46s |
| **Gate 7** | Frontend Vitest Unit & Component Suite | `npm test -- --run` | 498 tests (47 files) | **PASS** | 6.30s |
| **Gate 8** | Frontend Production Build | `npm run build` (`tsc -b && vite build`) | Production bundle | **PASS** | 8.26s |
| **Gate 9** | Frontend Lint Verification | `npm run lint` | 147 files (0 errors, 56 warnings) | **PASS** | 0.63s |
| **Gate 10** | Playwright Headless Browser Acceptance | `npx playwright test tests/e2e/*.e2e.ts` | 8 browser tests | **PASS** | 20.06s |

**Overall Acceptance Status:** `IMPLEMENTED_AND_VERIFIED` (All 10 gates passed with exit code 0).

---

## 4. Preservation of Core Invariants

1. **Trade Strategy & Detection Logic:** Zero alterations to SMC detection, sweeps, displacement, FVG, order blocks, or liquidity targeting.
2. **Risk & Geometry Integrity:** Preserved minimum R:R floor (1.5), commission/fee accounting, and order price geometry (TP < Entry < SL for SHORT; TP > Entry > SL for LONG).
3. **Execution Caps:** Strictly enforced max 3 fills per UTC+7 trading day, max 1 open position, and max 1 armed order.
4. **Quota Independence:** Under `ALL_SESSIONS_SETUP_FIRST`, NY quota requirements remain strictly `NOT_APPLICABLE` (0 fills required, no slot reservation).
5. **UI & Data Invariance:** Clean settings modal integration, zero direction POST on session save, historical orders and journal entries completely untouched.

---

## 5. Cutover Instructions for User / Operator

Since this environment is an automated testing/build environment (`CUTOVER_NOT_OBSERVED`):
1. Open the running AURUM web application in your browser.
2. Click the settings gear icon on the **CG-01 Diagnostic Card**.
3. Confirm that the timing mode dropdown is set to **Mọi phiên (Setup-first)**.
4. Click **Lưu phiên** to persist the configuration.
5. Verify that the notification badge confirms "Mọi phiên (Setup-first)" with version incremented and daily quota indicating 0/3 fills.
