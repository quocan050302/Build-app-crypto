# AURUM V17.4 — IMPLEMENTATION MAP
**Target Architecture:** Setup-First Paper Entries Across All Sessions (Asia, London, New York)  
**Branch:** `fix/v14-1-rr-flow`  
**Repository:** `quocan050302/Build-app-crypto`  
**Baseline SHA:** `4977a21deb475bafb0cd8cac6af87a4a0191d15a`  
**Scope:** PAPER Execution Only. Live execution scope is strictly rejected at resolver, schema, and route boundaries.

---

## 1. Domain Constants, Enums & Policies

### 1.1 `ALL_SESSIONS_SETUP_FIRST` [MỚI]
- **Location:** `backend/policy_constants.py`
- **Description:** Canonical enum constant representing the non-prioritised, setup-first entry policy across Asia, London, and New York.
- **Consumers:**
  - `backend/services/trading_policy_service.py`
  - `backend/services/entry_session_policy_service.py`
  - `backend/schemas.py`
  - `frontend/src/NYSessionPanel.tsx`
  - `frontend/src/utils/nyQuotaLabels.ts`
  - `frontend/src/components/TradeDetailsDrawer.tsx`
- **Tests:** `backend/tests/test_v17_4_contracts.py`

### 1.2 `CG01TimingProfile.CG01_ALL_SESSIONS_PAPER` [MỚI]
- **Location:** `backend/services/cg01_types.py`
- **Description:** Enum expansion for CG-01 timing profiles allowing paper execution across all trading sessions.
- **Consumers:**
  - `backend/services/cg01_engine.py::resolve_cg01_windows_for_profile`
  - `backend/services/scalp_m1_types.py::M1ScalpConfig`
  - `backend/services/entry_session_policy_service.py`
  - `frontend/src/components/CG01DiagnosticCard.tsx`
- **Tests:** `backend/tests/test_v17_4_timing_profiles.py`

---

## 2. Timing & Execution Windows

### 2.1 `cg01_engine.resolve_cg01_windows_for_profile` [HIỆN CÓ — MỞ RỘNG]
- **Caller:** `cg01_engine.resolve_cg01_time_context`
- **Inputs:** `dt_ny: datetime`, `profile: str`, `execution_scope: str = "PAPER"`
- **Outputs:** `Tuple[str, Optional[datetime], Optional[datetime], str]` -> `("ALL_SESSIONS_PAPER", None, None, "CG01_ALL_SESSIONS_PAPER")`
- **Transaction Owner:** None (Pure calculation)
- **Scope Defense:** Rejects live scopes (`LIVE`, `REAL`, `LIVE_EXECUTION`) with `ValueError("PAPER_ONLY_SESSION_PROFILE")`.
- **Tests:** `backend/tests/test_v17_4_timing_profiles.py`

### 2.2 `cg01_engine.resolve_cg01_time_context` [HIỆN CÓ — MỞ RỘNG]
- **Caller:** `cg01_engine.evaluate_cg01_candidate`, `ScalpWatcherOrchestrator.evaluate_cycle`
- **Inputs:** `now_ms: int`, `config: CG01Config`, `symbol: str`, `scope: str`
- **Outputs:** `CG01TimeContext` with `is_in_window = True`, `window_start_ms = None`, `window_end_ms = None`, clear Vietnamese diagnostics.
- **Transaction Owner:** None (Pure calculation)
- **Tests:** `backend/tests/test_v17_4_timing_profiles.py`

---

## 3. Session Policy & Quota Resolvers

### 3.1 `trading_policy_service.resolve_entry_session_context` [HIỆN CÓ — SỬA ĐỔI]
- **Caller:** `ScalpWatcherOrchestrator.evaluate_cycle`, `entry_guard_service`, `TradeLifecycleService`
- **Inputs:** `policy: TradingPolicy`, `strategy_family: str`, `now_ms: Optional[int]`, `symbol: str`
- **Outputs:** `EntrySessionContext`
- **Modifications:**
  - Explicitly mapped `ModelIdentifier.CG01_SWEEP_MSS_FVG.value` into `standard_families` (preventing fallback into legacy default NY restriction).
  - For `ALL_SESSIONS_SETUP_FIRST`: sets `standard_window_restriction = None`, `expiry_cap_ms = None`, leaving session analytical tracking intact while removing NY restriction.
- **Transaction Owner:** None (Pure domain resolver)
- **Tests:** `backend/tests/test_v17_4_entry_session_policy.py`

### 3.2 `trading_policy_service.evaluate_entry_policy` [HIỆN CÓ — SỬA ĐỔI]
- **Caller:** `ScalpWatcherOrchestrator._arm_paper_candidate`, `ExecutionCoordinator.evaluate_orders_sync`
- **Inputs:** `db: Session`, `now_ms: int`, `symbol: str`, `account_scope: str`, `policy_override: Optional[TradingPolicy]`
- **Outputs:** `PolicyCheckResult`
- **Modifications:**
  - Branch for `ALL_SESSIONS_SETUP_FIRST`: NY slot reservation bypassed (`reserved_slots = 0`). Only daily fill cap (3 fills/day) and standard account/drawdown/news guards apply.
- **Transaction Owner:** Read-only caller transaction.
- **Tests:** `backend/tests/test_v17_4_entry_session_policy.py`, `backend/tests/test_v17_4_concurrency.py`

### 3.3 `trading_policy_service.evaluate_daily_frequency_goal` [HIỆN CÓ — SỬA ĐỔI]
- **Caller:** Diagnostic pipeline, daily quota monitoring
- **Inputs:** `db: Session`, `now_ms: int`, `symbol: str`
- **Outputs:** `DailyFrequencyGoal`
- **Modifications:**
  - For `ALL_SESSIONS_SETUP_FIRST`, phase `AFTER_WINDOW` in NY does not mark trading day ended. Day boundary is anchored to configured `daily_timezone` (`Asia/Ho_Chi_Minh`).
- **Tests:** `backend/tests/test_v17_4_frequency.py`

### 3.4 `trading_policy_service.upgrade_session_policy_contract` [HIỆN CÓ — SỬA ĐỔI]
- **Caller:** `main.lifespan` -> `apply_runtime_contract_upgrades`
- **Inputs:** `db: Session`, `target_symbol: str`, `migration_id: str`, `desired_mode: str`
- **Outputs:** Migration report dict
- **Modifications:**
  - Protected `ALL_SESSIONS_SETUP_FIRST` from being overwritten by legacy `V14_4_5_ALL_SESSIONS_NY_RESERVE` migration on startup.
- **Tests:** `backend/tests/test_v17_4_startup.py`

---

## 4. Coordinated Atomic Persistence Service

### 4.1 `entry_session_policy_service.resolve_entry_session_settings` [MỚI]
- **Caller:** `entry_session_policy_service.update_entry_session_settings`, `GET /api/v1/policy/entry-session`
- **Inputs:** `policy: Optional[TradingPolicy]`, `scalp_config: Optional[M1ScalpConfig]`, `execution_scope: str = "PAPER"`
- **Outputs:** `EntrySessionSettingsResponse`
- **Transaction Owner:** None (Pure resolver)
- **Tests:** `backend/tests/test_v17_4_config_transaction.py`

### 4.2 `entry_session_policy_service.stage_entry_session_settings` [MỚI]
- **Caller:** `update_entry_session_settings`, `TradingPolicyService.update_policy`
- **Inputs:** `db: Session`, `patch: Dict[str, Any]`, `current_policy: TradingPolicy`, `current_scalp: M1ScalpConfig`, `now_ms: int`
- **Outputs:** `Tuple[TradingPolicy, M1ScalpConfig, List[str]]`
- **Transaction Owner:** Caller (Does NOT commit; stages in session)
- **Tests:** `backend/tests/test_v17_4_config_transaction.py`

### 4.3 `entry_session_policy_service.update_entry_session_settings` [MỚI]
- **Caller:** `main.py::PUT /api/v1/policy/entry-session`
- **Inputs:** `db: Session`, `request: EntrySessionSettingsUpdateRequest`, `symbol: str = "XAUUSDT"`
- **Outputs:** `EntrySessionSettingsResponse`
- **Transaction Owner:** This function owns the single atomic `db.commit()` and handles rollback on failure. Enforces concurrency via `expected_version` (409 conflict detection).
- **Tests:** `backend/tests/test_v17_4_config_transaction.py`, `backend/tests/test_v17_4_api.py`

---

## 5. Expiry & Candidate Order Lifetime

### 5.1 `cg01_types.resolve_candidate_order_expiry` [HIỆN CÓ — SỬA ĐỔI]
- **Caller:** `cg01_engine.evaluate_cg01_candidate`, `ScalpWatcherOrchestrator._arm_paper_candidate`
- **Inputs:** `cand: CG01Candidate`, `now_ms: int`, `session_context: Optional[EntrySessionContext]`
- **Outputs:** `int` (Authoritative UTC timestamp in ms)
- **Modifications:**
  - Standardized contract to read `EntrySessionContext.expiry_cap_ms` rather than inconsistent `window_end_ms`.
  - When `expiry_cap_ms` is None (in `ALL_SESSIONS_SETUP_FIRST`), expiry strictly honors model TTL / valid_until without synthetic session cuts.
- **Tests:** `backend/tests/test_v17_4_expiry.py`

---

## 6. API Endpoints

### 6.1 `GET /api/v1/policy/entry-session` [MỚI]
- **Location:** `backend/main.py`
- **Handler:** `get_entry_session_settings_endpoint`
- **Caller:** Frontend `useEntrySessionSettings` hook
- **Outputs:** `EntrySessionSettingsResponse` (Read-only, zero side-effects)
- **Tests:** `backend/tests/test_v17_4_api.py`

### 6.2 `PUT /api/v1/policy/entry-session` [MỚI]
- **Location:** `backend/main.py`
- **Handler:** `update_entry_session_settings_endpoint`
- **Caller:** Frontend `useEntrySessionSettings` hook
- **Inputs:** `EntrySessionSettingsUpdateRequest`
- **Outputs:** `EntrySessionSettingsResponse`
- **Tests:** `backend/tests/test_v17_4_api.py`

### 6.3 `PUT /api/scalp/config` [HIỆN CÓ — ĐỒNG BỘ]
- **Location:** `backend/main.py`
- **Handler:** `update_scalp_config_endpoint`
- **Modifications:**
  - Added support for `CG01_ALL_SESSIONS_PAPER`.
  - Stages `TradingPolicy` updates atomically in the same transaction if `ALL_SESSIONS_SETUP_FIRST` pairing is requested, preventing split-brain configuration states.
- **Tests:** `backend/tests/test_v17_4_api.py`

---

## 7. Frontend Integration

### 7.1 `frontend/src/api/client.ts` [HIỆN CÓ — MỞ RỘNG]
- Added `getEntrySessionSettings()` and `updateEntrySessionSettings()`.

### 7.2 `frontend/src/hooks/useEntrySessionSettings.ts` [MỚI]
- Manages draft selection, server confirmation, error state, optimistic concurrency (`expected_version`), and AbortController request deduplication.

### 7.3 `frontend/src/components/CG01DiagnosticCard.tsx` [HIỆN CÓ — MỞ RỘNG]
- Integrated coordinated timing selector with option `"Mọi phiên — chỉ vào khi đủ setup (PAPER)"`.
- Rendered compact summary badge: `"PAPER • Mọi phiên • Tối đa 3 lệnh/ngày"`.

### 7.4 `frontend/src/NYSessionPanel.tsx` & `nyQuotaLabels.ts` [HIỆN CÓ — MỞ RỘNG]
- Renders "Không ưu tiên phiên Mỹ" and "Không đặt chỉ tiêu NY" when `ALL_SESSIONS_SETUP_FIRST` is active.

### 7.5 `frontend/src/components/TradeDetailsDrawer.tsx` [HIỆN CÓ — MỞ RỘNG]
- Displays `"Mọi phiên (Setup-first)"` in trade policy badge.

---

## 8. Verification Matrix

| Suite | Component | Tests | Status |
|---|---|---|---|
| `test_v17_4_timing_profiles.py` | Timing Profiles & Scope Defense | 5/5 | PASS |
| `test_v17_4_contracts.py` | Enums, Schemas & Zero Quotas | 16/16 | PASS |
| `test_v17_4_entry_session_policy.py` | Entry Context & Reservation Bypassing | 4/4 | PASS |
| `test_v17_4_config_transaction.py` | Atomic DB Persistence & Rollback | 4/4 | PASS |
| `test_v17_4_api.py` | Entry Session API Endpoints | 4/4 | PASS |
| `test_v17_4_startup.py` | Contract Migration Safety | 2/2 | PASS |
| `test_v17_4_expiry.py` | Normalized Expiry Cap & TTL | 5/5 | PASS |
| `test_v17_4_frequency.py` | Trade Day Boundary & Frequency | 2/2 | PASS |
| `test_v17_4_guard_regression.py` | Pre-Arm & Pre-Fill Guard Gates | 4/4 | PASS |
| `test_v17_4_concurrency.py` | Concurrency & Race Invariance | 2/2 | PASS |
| `test_v17_4_production_flow.py` | Causal Production E2E Across 3 Sessions | 11/11 | PASS |
| `test_v14_4_*.py` | Legacy Session & Lifecycle Regression | 493/493 | PASS |
| Frontend Unit (`vitest`) | Frontend Client, Hooks, Panels & Stores | 488/488 | PASS |
| Frontend Build & Lint | TypeScript / Vite Build & Oxlint | 0 Errors | PASS |
| Playwright E2E | Browser Acceptance & Responsive Layout | 6/6 | PASS |
