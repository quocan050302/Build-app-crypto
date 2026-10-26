# V15.6 — DURABLE HYPOTHESIS PERSISTENCE CORRECTNESS CLOSURE REPORT

**Repository:** `quocan050302/Build-app-crypto`  
**Branch:** `fix/v14-1-rr-flow`  
**Baseline SHA:** `abc7332e`  
**Targeted Suites:** 78 backend test files (754 passed, 0 failed)  
**V15.6 Suite:** `backend/tests/test_v15_6_hypothesis_persistence.py` (31 passed, 0 failed)  

---

## 1. Executive Summary & Defect Root Causes

The reviewer independently reproduced two critical defects in the durable hypothesis persistence layer:

### Defect A: Expired Durable Hypothesis without Zone in Watchlist
- **Root Cause:** When an active durable hypothesis reached its expiry deadline but its source zone was no longer in the active watchlist (pruned, aged out, or omitted by extractor), `evaluate_cycle()` attempted to transition it to `EXPIRED` by fabricating a synthetic `LiquidityHypothesis` object with `WatchedZone(top=0.0, bottom=0.0)`. This caused:
  1. A `NameError` due to missing domain imports (`LiquidityHypothesis`, `WatchedZone`).
  2. A contract violation error (`ValueError: WatchedZone top must be greater than bottom: 0.0 <= 0.0`).
- **Fix:** Expire durable records directly via `stage_hypothesis_status_transition(db, symbol, record_or_identity, desired_status="EXPIRED", now_ms=...)`. No synthetic domain geometry (`WatchedZone`) or mock dealing range is ever fabricated. All original anchors (`hypothesis_id`, `created_at_ms`, `expiry_ms`, `source_fingerprint`, `revision`) are strictly preserved.

### Defect B: Corrupt Durable Record Re-creation and Overwrite
- **Root Cause:** 
  1. `load_durable_hypotheses_for_symbol()` silently skipped corrupt records (`continue`), returning an incomplete dictionary.
  2. The caller treated missing records as `NOT_FOUND`.
  3. `build_liquidity_hypotheses()` generated a fresh hypothesis with a renewed lifetime (`created_at = now`, `expiry = now + TTL`).
  4. `stage_hypothesis_lifecycle()` executed a blind `db.merge()`, overwriting the corrupt database record and erasing corrupted state evidence.
  5. The cycle returned `status: "OK"` despite persistence authority being compromised.
- **Fix:** 
  1. Introduced `HypothesisBatchLoadResult` and `HypothesisLoadIssue` with explicit statuses (`COMPLETE`, `PARTIAL`, `READ_FAILED`, `CORRUPT`, `UNSUPPORTED_VERSION`).
  2. Extracted pure parser `parse_hypothesis_lifecycle_row(key, raw_value, expected_symbol)`.
  3. Batch loader reports issues with `blocked_keys` and `blocked_identities`.
  4. Orchestrator purges stale in-memory cache against blocked identities/keys and fails closed on zone candidate evaluation with explicit reason codes (`HYPOTHESIS_STATE_CORRUPT`, `HYPOTHESIS_SCHEMA_UNSUPPORTED`, `HYPOTHESIS_STORE_UNAVAILABLE`).
  5. Raw persisted `SystemConfig` record value and `updated_at` remain byte-for-byte identical (no overwrite, no auto-delete, no fabrication).
  6. Double defense in `stage_hypothesis_lifecycle()`: explicitly queries existing DB key before staging, rejecting blind merge if the existing row is corrupt, has incompatible identity, or is in terminal state.

---

## 2. Structured Return Contracts

### 2.1 `HypothesisLoadStatus`
```python
class HypothesisLoadStatus(str, Enum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    CORRUPT = "CORRUPT"
    UNSUPPORTED_VERSION = "UNSUPPORTED_VERSION"
    READ_FAILED = "READ_FAILED"
```

### 2.2 `HypothesisLoadIssue`
```python
@dataclass(frozen=True)
class HypothesisLoadIssue:
    storage_key: str
    status: HypothesisLoadStatus
    reason: str
    hypothesis_id: Optional[str] = None
    schema_version: Optional[int] = None
```

### 2.3 `HypothesisBatchLoadResult`
```python
@dataclass
class HypothesisBatchLoadResult:
    records: Dict[str, DurableHypothesisRecord]
    issues: List[HypothesisLoadIssue]
    read_status: HypothesisLoadStatus
    blocked_keys: Set[str]
    blocked_identities: Set[str]
    diagnostics: Dict[str, Any]
```

### 2.4 `LifecycleStageResult`
```python
class LifecycleStageResult(str, Enum):
    STAGED = "STAGED"
    CREATED = "CREATED"
    UPDATED = "UPDATED"
    NO_CHANGE = "NO_CHANGE"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
```

---

## 3. Architecture & Purity Boundaries

| Function / Component | Boundary | Staging / Transaction Role | Invariants Enforced |
|---|---|---|---|
| `parse_hypothesis_lifecycle_row` | Pure | Read-only | Zero I/O, strict numeric validation, symbol namespace check, fingerprint verification. |
| `load_durable_hypotheses_for_symbol` | Query | Read-only | Never mutates rows, never commits; returns structured `HypothesisBatchLoadResult`. |
| `stage_hypothesis_status_transition` | Helper | Stage-only | Never commits; validates record authority, monotonic terminal state, updates only `status` and `updated_at_ms`. |
| `stage_hypothesis_lifecycle` | Writer | Stage-only | Never commits; double defense against corrupt keys, preserves original anchors (`created_at_ms`, `expiry_ms`). |
| `evaluate_cycle` | Orchestrator | Owner commit | Transaction owner; evaluates candidates, filters blocked keys/identities, commits atomically, invalidates cache on rollback. |
| `TradeLifecycleService.execute_close` | Exit Monitor | Independent owner | Unaffected by hypothesis store degradation; ensures open position exits are never blocked. |

---

## 4. Verification Evidence

### 4.1 Focused V15.6 Test Suite (`test_v15_6_hypothesis_persistence.py`)
- **31 test cases:** 31 passed, 0 failed in 0.77s.
  - `test_exact_repro_a_expired_durable_hypothesis_without_zone`: PASSED.
  - `test_empty_watchlist_with_overdue_record`: PASSED.
  - `test_multiple_overdue_and_terminal_records`: PASSED.
  - `test_direct_status_transition_helper`: PASSED.
  - `test_expire_commit_failure_and_rollback`: PASSED.
  - `test_exact_repro_b_corrupt_record_no_recreation_or_overwrite`: PASSED.
  - `test_corrupt_json_issue_propagation`: PASSED.
  - `test_known_id_schema_corrupt_and_unsupported_version`: PASSED.
  - `test_fingerprint_mismatch_fails_closed`: PASSED.
  - `test_key_payload_and_symbol_mismatch`: PASSED.
  - `test_numeric_corruption_validation` (10 parameterized cases): ALL PASSED.
  - `test_empty_store_returns_complete`: PASSED.
  - `test_db_query_failure_returns_read_failed`: PASSED.
  - `test_mixed_batch_valid_and_corrupt`: PASSED.
  - `test_direct_writer_defense_against_corrupt_key`: PASSED.
  - `test_stale_writer_cannot_downgrade_terminal_state`: PASSED.
  - `test_restart_after_corrupt_state_remains_blocked`: PASSED.
  - `test_cache_cannot_override_db_corruption`: PASSED.
  - `test_shadow_mode_with_corrupt_record`: PASSED.
  - `test_existing_position_exit_management_unaffected`: PASSED.
  - `test_hypothesis_api_endpoint_data_quality`: PASSED.
  - `test_acceptance_runner_registration_assertion`: PASSED.

### 4.2 Adjacent Suites Regression
- `test_v15_5_lifecycle_closure.py`: 13 passed, 0 failed.
- `test_v15_4_causal_entry.py`: 26 passed, 0 failed.
- `test_v15_3_causal_wiring.py`: 10 passed, 0 failed.
- **Combined adjacent suites:** 49 passed, 0 failed in 1.01s.

### 4.3 Full Acceptance Runner
- **Command:** `python tools/run_v14_4_2_acceptance.py`
- **Targeted Backend Files:** 78 files (Baseline: 77 files, 723 passed).
- **Backend Targeted Count:** 754 passed, 0 failed in 44.02s.
- **Frontend Checks:**
  - `frontend_typecheck`: exit 0
  - `frontend_lint`: exit 0
  - `frontend_vitest`: exit 0
  - `frontend_build`: exit 0

---

## 5. Non-Negotiable Constraints & Safety Guarantees

1. **Zero Strategy Tuning:** `STANDARD_SMC` M5, `SMC_M1_REVERSAL`, and `SMC_M1_CONTINUATION` rule parameters remain untouched.
2. **Strict Paper Only:** Zero real exchange calls; zero real money risks.
3. **No Financial Mutation:** Exit monitor and open paper positions operate independently; persistence metadata errors do not prevent closing open positions.
4. **Byte-for-Byte Raw Preservation:** Corrupted database entries in `SystemConfig` are never overwritten, cleaned, or modified.
5. **No Synthetic Zones:** WatchedZone geometry contracts (`top > bottom`) are strictly respected; no dummy `WatchedZone(0, 0)` is ever created.
