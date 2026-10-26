# V15.7 LIFECYCLE WRITE AUTHORITY REPORT
**Repair Lifecycle Write Authority, Entry Gating, Transaction Outcomes, and Cache Publication**

- **Repository:** `quocan050302/Build-app-crypto`
- **Branch:** `fix/v14-1-rr-flow`
- **Baseline SHA:** `9ea9157328f6c17e04b4312ac9233ab4a5ae351a`
- **Version:** V15.7
- **Date:** 2026-10-15

---

## 1. Executive Summary & Defect Analysis

### The Defect in Baseline (V15.6)
In `ScalpWatcherOrchestrator.evaluate_cycle()`, the staging loop for liquidity hypotheses called `stage_hypothesis_lifecycle(db, symbol, h, now_ms=t_recv)`.
When the writer returned `FAILED` or `BLOCKED`:
1. The failure was silently discarded.
2. The orchestrator proceeded to evaluate reversal models for the zone associated with the failed hypothesis.
3. Candidate reversal signals reached `_arm_paper_candidate()`, which had no check for lifecycle staging success.
4. An armed `PaperOrder` was created and persisted, despite zero durable lifecycle records (`hlc:...`) existing in the database.
5. The cycle status reported `"OK"`, masking the persistence failure.
6. The uncommitted or failed hypotheses were published into `self._active_hypotheses[symbol]`, exposing non-authoritative hypotheses to consumers and subsequent cycles.

### Target Fix (V15.7)
1. **Explicit Write Authority Tracking:** Introduce `HypothesisWriteOutcome` and `LifecycleCycleAuthority` to record outcomes for every hypothesis and status transition in the cycle.
2. **Pre-Model Reversal Gating:** Before `evaluate_m1_reversal_candidate()` is invoked, verify that the zone has an entry-authorized hypothesis with successful write outcome.
3. **Arm-Boundary Defense:** In `_arm_paper_candidate()`, call `check_candidate_lifecycle_authority(cand, authority_context)` before creating `WatchSetup`, scenario snapshot, or `PaperOrder`.
4. **Transaction Atomicity:** Commit the cycle transaction only when complete; on commit failure, execute `db.rollback()`, invalidate the armed order, degrade status, and clear cache.
5. **Post-Commit Authoritative Cache Publication:** `self._active_hypotheses[symbol]` is only populated after successful `db.commit()` and only contains `entry_authorized_hypotheses`.
6. **Deterministic Cycle Status:** `derive_cycle_status()` synthesizes read status, write outcomes, and commit status without false `"OK"` reports.

---

## 2. Before & After Reproduction

### Reproduction Script
```python
with patch("services.liquidity_scenario_service.stage_hypothesis_lifecycle", return_value=LifecycleStageResult.FAILED):
    res = orch.evaluate_cycle(
        db, "XAUUSDT", now_ms=1300000, quote=quote, candles_1m=candles_1m,
        config=base_cfg, feed_health={"status": "CONNECTED", "latency_ms": 10}, auto_enabled=True
    )
```

### Baseline Outcome (Before Patch)
- `res["armed_order_id"]`: `'ord_SMC_M1_REVERSAL_...'` (ARMED)
- `db.query(PaperOrder).count()`: `1`
- `db.query(SystemConfig).filter(key.like("hlc:%")).count()`: `0`
- `res["status"]`: `'OK'` (FALSE OK)
- `len(orch.get_active_hypotheses("XAUUSDT"))`: `1` (POISONED CACHE)

### Patched Outcome (After V15.7 Patch)
- `res["armed_order_id"]`: `None` (BLOCKED)
- `db.query(PaperOrder).count()`: `0`
- `db.query(WatchSetup).count()`: `0`
- `res["status"]`: `'DEGRADED'`
- `res["lifecycle_write_status"]`: `'FAILED'`
- `res["lifecycle_write_issues_count"]`: `>= 1`
- `len(orch.get_active_hypotheses("XAUUSDT"))`: `0` (CLEAN CACHE)

---

## 3. Production Code Modifications

### 1. `backend/services/scalp_m1_types.py`
- Added new enum values to `OpportunityReasonCode`:
  * `HYPOTHESIS_WRITE_BLOCKED = "HYPOTHESIS_WRITE_BLOCKED"`
  * `HYPOTHESIS_WRITE_FAILED = "HYPOTHESIS_WRITE_FAILED"`
  * `HYPOTHESIS_AUTHORITY_MISSING = "HYPOTHESIS_AUTHORITY_MISSING"`

### 2. `backend/services/scalp_m1_model_service.py`
- Added optional `hypothesis_id: Optional[str] = None` attribute to `ScalpCandidate`.

### 3. `backend/services/liquidity_scenario_service.py`
- Added pure helper `is_lifecycle_stage_success(result: Any) -> bool`:
  Returns `True` exclusively for `LifecycleStageResult.CREATED`, `UPDATED`, `NO_CHANGE`, `STAGED`. Returns `False` for `BLOCKED`, `FAILED`, `None`, and invalid types.
- Added dataclasses:
  * `HypothesisWriteOutcome(hypothesis_id, storage_key, zone_id, operation, result, is_success, reason_code, message)`
  * `LifecycleCycleAuthority`: aggregates per-cycle write outcomes, tracks `has_write_failures`, `has_write_blocks`, and computes `write_status` (`OK`, `BLOCKED`, `FAILED`).
- Added deterministic status resolver:
  * `derive_cycle_status(read_status, write_status, commit_ok, has_failed_write, has_blocked_write) -> str`
- Added candidate authority validator:
  * `check_candidate_lifecycle_authority(cand: ScalpCandidate, authority_context: Optional[Any]) -> Tuple[bool, Optional[str]]`:
    Enforces that reversal models require a validated, successful write outcome. Independent models (e.g. `SMC_M1_CONTINUATION`, `NY_DAILY_RESEARCH`) pass without requiring reversal hypothesis authority.

### 4. `backend/services/scalp_watcher_orchestrator.py`
- Staging Loop:
  * Records `HypothesisWriteOutcome` for every hypothesis and records overdue transitions.
  * Filters hypotheses into provisional `entry_authorized_hypotheses`.
  * Logs structured diagnostic info for all write failures.
- Reversal Model Pre-Filter:
  * Inspects `cycle_authority.zone_outcomes` before calling `evaluate_m1_reversal_candidate()`.
  * If a zone is blocked or failed, records an `OpportunityEvaluation` in `TRIGGER_PENDING` with the appropriate reason code and skips model evaluation.
- Transaction Ownership & Commit Handling:
  * Manages single transaction commit at cycle end.
  * On `Exception` during `db.commit()`: rolls back, sets `armed_order = None`, clears active cache, and marks transaction status as `FAILED`.
- Post-Commit Cache Publication:
  * `self._active_hypotheses[symbol] = entry_authorized_hypotheses` executed exclusively when `commit_ok` is `True`.
- Arm Boundary Guard:
  * `_arm_paper_candidate()` checks `check_candidate_lifecycle_authority(cand, authority_context)` before any `WatchSetup`, scenario snapshot, or `PaperOrder` record is created.
  * Normalizes dictionary quotes into `ValidatedQuote` for canonical entry guard evaluation.
  * Added `get_latest_hypothesis_authority(symbol)` accessor.

### 5. `backend/main.py`
- Updated `/api/scalp/hypotheses` endpoint to integrate `write_status` and `write_issues_count` into its response while preserving backward compatibility.

### 6. `tools/run_v14_4_2_acceptance.py`
- Added `"tests/test_v15_7_lifecycle_write_authority.py"` to `targeted_files` (now 79 targeted backend files).

---

## 4. Invariants Preserved

1. **SMC & Core Algorithms:** Zero changes to SMC structure, OB, FVG, sweep, displacement, or target resolver algorithms.
2. **Session Policies:** Preserved user-configured trading sessions (`ALL_SESSIONS_WITH_NY_RESERVE`, etc.).
3. **Risk & Sizing:** Zero modifications to leverage, margin mode, capital, risk percentage, or calculator calculations.
4. **SL / TP Geometry:** Enforced strict SL/TP orientation (`LONG`: SL < entry < TP; `SHORT`: TP < entry < SL).
5. **Canonical Guards:** Canonical entry guards (news, lessons, quote freshness, feed connection, daily loss budget, daily trade cap) remain active.
6. **No Live Data:** All tests run exclusively against SQLite in-memory databases with zero live exchange or Telegram calls.
7. **Active Position Isolation:** Managing open positions (TP/SL monitoring and reconciliation) is completely isolated from new entry gating.

---

## 5. Verification & Test Suite

- **New Test Suite:** `backend/tests/test_v15_7_lifecycle_write_authority.py` (40 tests, all passing):
  * Causal LONG regression: FAILED writer blocks entry, prevents orders, and degrades status.
  * Causal LONG regression: BLOCKED writer records `HYPOTHESIS_WRITE_BLOCKED` and sets status `PARTIAL`.
  * Causal SHORT regressions: Symmetrical failure-closed behavior for SHORT setups.
  * Positive controls: Unmocked real writer successfully arms LONG and SHORT setups.
  * Unit tests: Exhaustive parameterization of `is_lifecycle_stage_success`.
  * Boundary tests: Unknown results, query exceptions, flush exceptions, and commit failures cleanly roll back.
  * Multi-zone tests: One blocked zone does not compromise independent valid zones; writer failure halts cycle entry.
  * Model independence: Continuation and Daily NY research models are not coerced into reversal hypotheses.
  * Arm direct invocation: Direct calls to `_arm_paper_candidate()` reject invalid or missing authority context.
  * Cache & Expiry: Cache never contains uncommitted or failed hypotheses; anchors and dead zones remain intact.
  * Status Matrix: Comprehensive coverage of read, write, and commit combinations.
