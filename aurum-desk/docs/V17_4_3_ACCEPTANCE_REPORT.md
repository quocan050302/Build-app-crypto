# AURUM V17.4.3 — Final Acceptance & Verification Report

**Implementation Status:** `IMPLEMENTED_AND_VERIFIED`  
**Base SHA:** `8085d05883960a823ec51b5b0072982eb65630f6`  
**Branch:** `fix/v14-1-rr-flow`  
**Commit Message:** `fix(v17.4.3): synchronize session goal mirrors across legacy writers`  
**Execution Scope:** PAPER ONLY (Authoritative database resolution; client scope overrides rejected)  
**Trading Invariants:** Max 3 fills/day (UTC+7 `Asia/Ho_Chi_Minh`), max 1 open position, max 1 armed order, zero NY preference/reservation in setup-first mode, R:R floor 1.5 preserved.

---

## 1. Executive Summary

AURUM V17.4.3 closes the bidirectional session synchronization defect when transitioning from **Setup-First (Mọi phiên)** back to **Legacy (Phiên Mỹ / Reserve)** across legacy writer routes.

### Root Cause Analysis & Resolutions
- **Root Cause A (`backend/main.py::update_scalp_config_endpoint`):**  
  When transitioning from `ALL_SESSIONS_SETUP_FIRST` to legacy timing (e.g., `SILVER_BULLET_LONDON_NY`), `cur_pol` set `ny_min_fills = 1`, `reserve_ny_slot = True`, `ny_fallback_enabled = True`. However, because `daily_ny_goal` was only written inside the subsequent `if has_daily_update:` block, a payload containing only `timing_profile` omitted updating `SystemConfig.daily_ny_goal`, leaving it stale at `'0'`.  
  **Resolution:** Consolidated mirror staging immediately before `db.commit()` using `stage_entry_session_config_mirrors(db, timing_profile=eff_timing, ny_goal=cur_pol.ny_min_fills, now_ms=now_ms)`.
- **Root Cause B (`backend/services/trading_policy_service.py::TradingPolicyService.update_policy`):**  
  When transitioning to `NY_ONLY` or `ALL_SESSIONS_WITH_NY_RESERVE`, the service updated `policy.ny_min_fills` and normalized the timing profile, but never synchronized `SystemConfig.daily_ny_goal` to match `policy.ny_min_fills`.  
  **Resolution:** Added unified staging via `stage_entry_session_config_mirrors` whenever `entry_session_policy` or `ny_min_fills` is present in validated request attributes.
- **Root Cause C (`backend/services/entry_session_policy_service.py`):**  
  Unified `stage_entry_session_config_mirrors` to strictly validate timing profile and non-negative integer NY goal, calling `_upsert_system_config_no_commit`. Hardened `_upsert_system_config_no_commit` to inspect `db.new` so sessions with `autoflush=False` do not produce duplicate row errors on repeated same-key staging.

---

## 2. Baseline Red Probes vs. Post-Fix Verification

| Probe ID / Scenario | Baseline Result (`8085d05`) | V17.4.3 Result | Verification Proof |
|---|---|---|---|
| **Red Probe 1 (Scalp Timing-Only)**: `PUT entry-session` (setup-first) → `POST scalp/config` (`SILVER_BULLET_LONDON_NY` timing only) | ❌ FAIL: `policy.ny_min_fills == 1`, but `SystemConfig.daily_ny_goal == '0'` (`assert '0' == '1'`) | ✅ PASS: Both `policy.ny_min_fills == 1` and `SystemConfig.daily_ny_goal == '1'` | `test_v17_4_3_session_roundtrip.py::test_new_to_legacy_persists_goal_mirror[scalp]` |
| **Red Probe 2 (Policy NY_ONLY)**: `PUT entry-session` (setup-first) → `POST policy/trading` (`NY_ONLY`, `ny_min_fills: 1`) | ❌ FAIL: `policy.ny_min_fills == 1`, but `SystemConfig.daily_ny_goal == '0'` (`assert '0' == '1'`) | ✅ PASS: Both `policy.ny_min_fills == 1` and `SystemConfig.daily_ny_goal == '1'` | `test_v17_4_3_session_roundtrip.py::test_new_to_legacy_persists_goal_mirror[policy]` |

---

## 3. Comprehensive Acceptance Gate Results

All 10 gates executed via automated runner `tools/run_v17_4_3_acceptance.py`:

| Gate | Scope | Command | Tests Run | Result | Duration |
|---|---|---|---|---|---|
| **Gate 1** | Bidirectional Roundtrip & Mirror Staging (D01-D16) | `pytest backend/tests/test_v17_4_3_session_roundtrip.py backend/tests/test_v17_4_3_atomic_mirrors.py` | 20 tests (D01-D16, atomic rollback, no-op) | **PASS** | 39.27s |
| **Gate 2** | Baseline Adversarial Probes (Q, N, L, C) | `pytest backend/tests/test_v17_4_2_baseline_probes.py` | 4 tests | **PASS** | 7.35s |
| **Gate 3** | Quote Revalidation & Execution Parity | `pytest backend/tests/test_v17_4_2_quote_revalidation.py backend/tests/test_v17_4_2_quote_execution.py` | 17 tests (Q01-Q09, QE01-QE08) | **PASS** | 20.40s |
| **Gate 4** | No-op Database Authority & Session Writer Contracts | `pytest backend/tests/test_v17_4_2_noop_authority.py backend/tests/test_v17_4_2_writer_contracts.py` | 20 tests (N01-N08, W01-W12) | **PASS** | 39.80s |
| **Gate 5** | V17.4.1 Full Regression Suite | `pytest backend/tests/test_v17_4_1_*.py` | 37 tests (CAS, flows, guards, scope, quota) | **PASS** | 68.92s |
| **Gate 6** | Selected Session & Startup Regressions | `pytest backend/tests/test_v14_4_*.py` | 32 tests (startup, 3-session entry/exec) | **PASS** | 5.65s |
| **Gate 7** | Frontend Vitest Unit & Component Suite | `npm test -- --run` | 498 tests (47 files) | **PASS** | 6.83s |
| **Gate 8** | Frontend Production Build | `npm run build` (`tsc -b && vite build`) | Production bundle | **PASS** | 9.25s |
| **Gate 9** | Frontend Lint Verification | `npm run lint` | 147 files (0 errors, 56 warnings) | **PASS** | 0.99s |
| **Gate 10** | Playwright Headless Browser Acceptance | `npx playwright test tests/e2e/*.e2e.ts` | 8 browser tests | **PASS** | 22.41s |

**Overall Acceptance Status:** `IMPLEMENTED_AND_VERIFIED` (All 10 gates passed with exit code 0).

---

## 4. Matrix of Verified Scenarios (D01 - D16)

- **D01 (PUT Legacy → Setup-First):** `ALL_SESSIONS_SETUP_FIRST`, timing `CG01_ALL_SESSIONS_PAPER`, policy goal 0, mirror `'0'`, reserve `False`, fallback `False`.
- **D02 (Scalp Timing-Only Setup-First → Legacy):** Timing `SILVER_BULLET_LONDON_NY`, mode `ALL_SESSIONS_WITH_NY_RESERVE`, policy goal 1, mirror `'1'`.
- **D03 (Policy Setup-First → NY_ONLY):** Mode `NY_ONLY`, policy goal 1, mirror `'1'`.
- **D04 (Custom Goal 2):** Mode legacy, explicit `ny_min_fills: 2`, policy goal 2, mirror `'2'`.
- **D05 (Legacy Explicit Goal 0):** Mode legacy, explicit `ny_min_fills: 0`, policy goal 0, mirror `'0'`.
- **D06 (Policy Omitted Goal):** Retains valid normalized goal, mirror synchronized.
- **D07 (Legacy Goal-Only Update):** Timing preserved, goal updated to 2, mirror `'2'`.
- **D08 (Conflicting Setup-First + NY Goal 1 via Scalp):** Rejected HTTP 400 `ENTRY_SESSION_CONFIG_CONFLICT`.
- **D09 (Server LIVE Scope Guard):** Setup-first rejected under `LIVE` database scope.
- **D10 (Malformed Inputs):** Strict integer validation rejects `bool`, negative, or float goals.
- **D11 (Missing Mirrors Creation):** Recreates missing mirror rows cleanly on write.
- **D12 (Autoflush=False Safe Upsert):** Repeated staging in same transaction produces exactly 1 row per key.
- **D13 (Rollback Integrity):** Fault injection before commit rolls back entire transaction cleanly.
- **D14 (Stale Expected Version PUT):** Rejected HTTP 409 `StalePolicyVersionError`.
- **D15 (True No-Op PUT):** Preserves version, timestamps, and does not emit audit events.
- **D16 (Invariant Preservation across Roundtrips):** Custom R:R (1.7), leverage, capital, daily direction, and open paper positions remain 100% intact through multiple bidirectional transitions.

---

## 5. Preservation of Core Invariants

1. **Trade Strategy & Detection Logic:** Zero alterations to SMC detection, sweeps, displacement, FVG, order blocks, or liquidity targeting.
2. **Risk & Geometry Integrity:** Preserved custom R:R floor (e.g. 1.7 / 1.5), commission/fee accounting, and order price geometry (TP < Entry < SL for SHORT; TP > Entry > SL for LONG).
3. **Execution Caps:** Strictly enforced max 3 fills per UTC+7 trading day, max 1 open position, and max 1 armed order.
4. **Quota Independence:** Under `ALL_SESSIONS_SETUP_FIRST`, NY quota requirements remain strictly `NOT_APPLICABLE` (0 fills required, no slot reservation).
5. **UI & Data Invariance:** Clean settings modal integration, zero direction POST on session save, historical orders and journal entries completely untouched.

---

## 6. Cutover Instructions for User / Operator

**Current Environment Status:** `CUTOVER_NOT_OBSERVED` (Development & test checkout environment).

To apply this verified configuration to your running app:
1. Open the running AURUM web application in your browser.
2. Click the settings gear icon on the **CG-01 Diagnostic Card**.
3. Confirm that the timing mode dropdown is set to **Mọi phiên (Setup-first)**.
4. Click **Lưu phiên** to persist the configuration.
5. Verify that the notification badge confirms "Mọi phiên (Setup-first)" with version incremented and daily quota indicating 0/3 fills.
6. If ever switching back to **Phiên Mỹ** or **Mọi phiên (Giữ slot NY)**, the database mirrors are now guaranteed to synchronize to NY goal 1 (or your custom goal) without requiring manual SQL fixes or app restarts.
