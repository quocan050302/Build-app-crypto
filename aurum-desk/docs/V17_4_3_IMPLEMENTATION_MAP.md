# AURUM V17.4.3 — Implementation Map & Architecture Specification

**Repository:** `quocan050302/Build-app-crypto`  
**Branch:** `fix/v14-1-rr-flow`  
**Baseline SHA:** `8085d05883960a823ec51b5b0072982eb65630f6`  
**Target Commit:** `fix(v17.4.3): synchronize session goal mirrors across legacy writers`  
**Scope:** Bidirectional Session Configuration Synchronization (SETUP-FIRST ↔ LEGACY roundtrips across all 3 writers; zero strategy alteration, zero threshold loosening, zero NY preference/reservation)

---

## 1. High-Level Call Flow Architecture

```mermaid
sequenceDiagram
    autonumber
    actor Caller as UI / API Client (PUT entry-session, POST policy, POST scalp)
    participant Writer as Writer Endpoint / Service Owner
    participant Validator as Input & Scope Validator
    participant Helper as stage_entry_session_config_mirrors [MỚI]
    participant DB as SQLite DB (TradingPolicy & SystemConfig)

    Note over Caller,DB: Bidirectional Session Staging & Mirror Synchronization
    Caller->>Writer: Request (Session Mode, Timing Profile, NY Min Fills / Daily NY Goal)
    Writer->>Validator: Validate server execution_scope (PAPER only for setup-first)
    Writer->>Validator: Validate strict integers & conflict invariants (no nonzero NY goal in setup-first)
    
    alt Setup-First to Legacy (Writer: POST /api/scalp/config)
        Writer->>Writer: Normalize cur_pol (mode=ALL_SESSIONS_WITH_NY_RESERVE, ny_min_fills=1, reserve=True, fallback=True)
        Writer->>Helper: stage_entry_session_config_mirrors(db, timing_profile=final_timing, ny_goal=1, now_ms=now)
        Helper->>DB: Upsert SystemConfig timing_profile & daily_ny_goal='1' (pending in db.new)
        Writer->>DB: db.commit()
        Writer-->>Caller: 200 OK (policy ny_min_fills=1, SystemConfig daily_ny_goal='1')
    else Setup-First to Legacy (Writer: POST /api/v1/policy/trading)
        Writer->>Writer: Apply validated attributes; normalize mode (NY_ONLY / RESERVE)
        Writer->>Helper: stage_entry_session_config_mirrors(db, timing_profile=final_timing, ny_goal=policy.ny_min_fills, now_ms=now)
        Helper->>DB: Upsert SystemConfig timing_profile & daily_ny_goal=str(ny_goal)
        Writer->>DB: db.commit()
        Writer-->>Caller: 200 OK (policy ny_min_fills=goal, SystemConfig daily_ny_goal=str(goal))
    else Coordinated Update (Writer: PUT /api/v1/policy/entry-session)
        Writer->>Writer: Atomic CAS with version fence
        Writer->>Helper: stage_entry_session_config_mirrors(db, timing_profile=plan.timing_profile, ny_goal=plan.ny_goal, now_ms=plan.updated_at_ms)
        Helper->>DB: Upsert SystemConfig timing_profile & daily_ny_goal=str(ny_goal)
        Writer->>DB: db.commit()
        Writer-->>Caller: 200 OK (version=expected+1, synchronized mirrors)
    end
```

---

## 2. Component & Function Implementation Matrix

| File | Function / Symbol | Type | Callers | Contract & Invariant | Test Reference | Status |
|---|---|---|---|---|---|---|
| `backend/services/entry_session_policy_service.py` | `_upsert_system_config_no_commit` | [HIỆN CÓ] (Hardened) | `stage_entry_session_config_mirrors`, internal writers | Checks `db.new` before querying DB. Prevents duplicate entity insertion when `autoflush=False` within the same transaction. | `test_v17_4_3_atomic_mirrors.py::test_same_key_staged_twice_autoflush_false` | VERIFIED |
| `backend/services/entry_session_policy_service.py` | `stage_entry_session_config_mirrors` | [MỚI] | `apply_entry_session_update_plan`, `TradingPolicyService.update_policy`, `update_scalp_config_endpoint` | Stages `timing_profile` and `daily_ny_goal` mirrors to `SystemConfig` in a single staging boundary without committing, rolling back, or bumping versions. Strictly validates `timing_profile` enum and non-negative integer `ny_goal` (rejects booleans/negatives). | `test_v17_4_3_atomic_mirrors.py::test_invalid_inputs_rejected_before_staging` | VERIFIED |
| `backend/services/entry_session_policy_service.py` | `apply_entry_session_update_plan` | [HIỆN CÓ] (Refactored) | `update_entry_session_settings` | Replaced individual `_upsert_system_config_no_commit` calls with `stage_entry_session_config_mirrors`. Preserves SQL version fence, fresh scalar checks, CAS mutation, and DomainEvent audit order. | `test_v17_4_3_session_roundtrip.py::test_d01_legacy_to_setup_first_put` | VERIFIED |
| `backend/services/trading_policy_service.py` | `TradingPolicyService.update_policy` | [HIỆN CÓ] (Synchronized) | `POST /api/v1/policy/trading` | When request touches `entry_session_policy` or `ny_min_fills`, determines `final_timing` and stages mirrors via `stage_entry_session_config_mirrors` with `policy.ny_min_fills`. Solves root cause B. | `test_v17_4_3_session_roundtrip.py::test_d03_setup_first_to_legacy_policy_ny_only` | VERIFIED |
| `backend/main.py` | `update_scalp_config_endpoint` | [HIỆN CÓ] (Synchronized) | `POST /api/scalp/config` | Consolidated mirror staging right before `db.commit()`. When `timing_profile` or `has_daily_update` is present, calls `stage_entry_session_config_mirrors` with `eff_timing` and `cur_pol.ny_min_fills`. Solves root cause A (timing-only new→legacy missing mirror). | `test_v17_4_3_session_roundtrip.py::test_d02_setup_first_to_legacy_scalp_timing_only` | VERIFIED |

---

## 3. Transaction Ownership, Invariants & Anti-Patterns Avoided

1. **Transaction Boundary & Single Owner:**
   - `stage_entry_session_config_mirrors` never commits, rolls back, or opens nested sessions.
   - The outer calling endpoint/service (`update_policy`, `update_scalp_config_endpoint`, or `update_entry_session_settings`) retains 100% ownership of transaction commit/rollback.
2. **Version Authority:**
   - Helper does not increment `policy.version` or `config_version`.
   - CAS endpoint increments version by exactly 1 on real mutation and 0 on no-op.
   - Legacy routes retain their existing single-owner version bump contracts.
3. **Pending Entity Safe Staging (`db.new`):**
   - In sessions where `autoflush=False`, calling upsert twice on the same key in one transaction updates the pending instance in `db.new` rather than attempting a duplicate `db.add()`, preventing `IntegrityError` or duplicate key failures.
4. **Canonical Source vs. Mirror Role:**
   - `TradingPolicy.ny_min_fills` remains the canonical source of truth for NY fill requirements (read by `resolve_effective_daily_policy`).
   - `SystemConfig.daily_ny_goal` is the exact compatibility mirror read by `load_effective_scalp_config`.
   - Both rows are guaranteed identical (`policy.ny_min_fills == int(SystemConfig.daily_ny_goal)`) immediately upon HTTP 200 response without requiring background repair loops or GET-based repairs.
5. **Zero Strategy / Zero Quota Alteration:**
   - Under `ALL_SESSIONS_SETUP_FIRST`, `ny_min_fills` is strictly 0 and `daily_ny_goal` is strictly `'0'`.
   - No historical quota snapshot or automatic legacy restoration is invented; transitions respect explicit user payloads or standard contract defaults.
