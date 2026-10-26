# AURUM DESK V17.7.3 — FUNCTION CHANGE MAP
**Detailed Code Changes, Signatures, Invariants & Impact Analysis**  
**Repository:** `quocan050302/Build-app-crypto`  
**Branch:** `feat/v17-7-adaptive-structural-stop-loss`  
**Status:** `VERIFIED_FROM_SOURCE`

---

## 1. Production Code Changes

### 1.1 `backend/database.py`
- **`TEST_ROOT_MARKER: str = ".aurum_test_owned"`**:
  - *New constant*: Identifies directory ownership by the test runner.
- **`get_or_create_test_root() -> Path`**:
  - *New function*: Creates an isolated test root directory under OS temporary storage (e.g. `aurum_test_root_XXXXXX/`) and initializes the metadata marker file with creation timestamp and PID.
- **`register_trusted_test_root(path: Path) -> Path`**:
  - *New function*: Registers external test-managed directories (e.g., pytest's session `basetemp`) as trusted roots after confirming or writing the ownership marker.
- **`assert_test_database_isolated(db_path: str = None) -> bool`**:
  - *Remediated*: Replaced basename-only check with canonical path resolution (`Path.resolve()`), URI scheme peeling (`sqlite:///`, `file://`), collision prevention with `RUNTIME_DEFAULT_DB_PATH`, and strict path containment check (`t_path.is_relative_to(root)`). Raises fail-closed `RuntimeError` if outside trusted test roots.
- **`run_schema_migrations(target_engine=None)`**:
  - *Remediated*: Added fail-closed guard calling `assert_test_database_isolated(db_file)` before executing any DDL or migration operations.

### 1.2 `backend/services/stop_placement_types.py`
- **`StopATRResult`**:
  - *Extended*: Added provenance metadata fields:
    - `symbol: str = "XAUUSDT"`
    - `source: str = "AGGREGATE_5M_SPLIT_OR_CLOSED_CANDLES"`
    - `source_candle_count: int = 0`
    - `last_closed_candle_timestamp: Optional[int] = None`
    - `calculated_as_of: Optional[int] = None`
    - `decision_cutoff: Optional[int] = None`
- **`StopBufferResult`**:
  - *Extended*: Added explicit unweighted ATR metrics:
    - `raw_atr_m1: Optional[float] = None`
    - `raw_atr_m5: Optional[float] = None`
    - Added `_UNSET` sentinel and `__post_init__` for backwards compatibility with legacy fixtures.

### 1.3 `backend/services/stop_placement_service.py`
- **`compute_stop_atr(...) -> StopATRResult`**:
  - *Remediated*:
    - Enforced symbol matching: returns `UNAVAILABLE` if candles belong to another symbol.
    - Enforced candle closure cutoff: excludes any candle whose close timestamp exceeds `as_of_ms`.
    - Enforced candle count: requires at least `period` closed candles.
    - Finite price verification: rejects NaN, infinite, or negative ATR values.
- **`compute_stop_buffer(...) -> StopBufferResult`**:
  - *Remediated*: Passes through `raw_atr_m1` and `raw_atr_m5` from the authoritative ATR results into `StopBufferResult`, keeping raw metrics separate from weighted buffer contributions.
- **`compute_min_protective_distance(...) -> float`**:
  - *Remediated*: Enforced that an explicit `StopATRStatus.UNAVAILABLE` in `atr_m1_obj` cannot be overridden by passing a loose scalar `atr_m1`.
- **`plan_structural_stop(...) -> StopPlacementResult`**:
  - *Remediated*:
    - Completely removed the defective fallback pathway:
      `buffer_result.components.get("atr_m1")`.
    - Enforced that `eff_m1` is strictly derived from `atr_m1_obj.value` or authoritative scalar.
    - Enforced fail-closed behavior under `PAPER_STRUCTURAL`: missing or non-positive M1 ATR returns `StopPlacementStatus.WAITING_VOLATILITY_EVIDENCE` and blocks ARM.
    - Verified distance-to-ATR ratio uses raw ATR: `dist / eff_m1`.

### 1.4 `backend/services/scalp_watcher_orchestrator.py`
- **`_arm_paper_candidate(...) -> Optional[PaperOrder]`**:
  - *Remediated*: Added `db.commit()` inside the `serialize_entry_mutation(active_session)` critical section before returning the order. Guarantees durable SQLite persistence so that concurrent threads reading active counts or idempotent keys see the committed order.

### 1.5 `backend/lab/v9_test_harness.py` & `backend/lab/ny_quota_frequency_audit.py`
- *Remediated*: Updated temporary database creation to place test directories inside `get_or_create_test_root()`, ensuring compliance with database safety isolation.

### 1.6 `backend/tests/e2e_server.py`
- *Remediated*: Updated E2E backend test launcher to place its database inside `get_or_create_test_root()`.

---

## 2. Test Infrastructure & Tooling Changes

### 2.1 `tools/acceptance_verdict.py` (New Module)
- **`MANDATORY_GATES_V17_7_3: List[str]`**: Defines the authoritative manifest of all 28 release gates.
- **`determine_release_verdict(actual_gate_results, mandatory_gate_manifest) -> str`**:
  Pure, fail-closed resolution function returning `"ACCEPTED_FOR_PAPER_OPT_IN"` only when all 28 mandatory gates are present with `status == "PASS"`, `exit_code == 0`, and zero test/collection failures.

### 2.2 `tools/run_v17_7_acceptance.py` (Upgraded Master Runner)
- Upgraded to V17.7.3 architecture covering all 28 release gates:
  - G01: Branch & Source Integrity
  - G02: Test-Owned Database Bootstrap
  - G03: Unsafe Database Path Rejection
  - G04: Destructive Test Fixture Isolation
  - G05: Raw ATR Provenance Authority
  - G06: Required M1 ATR Fail-Closed
  - G07: ATR / Buffer Mathematical Correctness
  - G08: Structural Anchor Causal Integrity
  - G09: Distant SL Financial Integrity
  - G10: CG01 Real Evaluator Integration
  - G11: Scalp M1 Models Real Evaluator Integration
  - G12: Orchestrator / Actual ARM Boundary
  - G13: Real ARM Concurrency & Idempotency
  - G14: Fill-Time Financial Invariants & Sizing
  - G15: PAPER Exit, CLOSED State & Persistence
  - G16: Actual Live-Order Dispatch Safety & Negative Control
  - G17: Dedicated V17.7 Suite (100 Gates)
  - G18: V17.5 Regression Suite
  - G19: V17.6 Regression Suite
  - G20: Entire backend/tests Pytest Suite
  - G21: Frontend Vitest Suite
  - G22: Frontend Production Build
  - G23: Frontend Lint (Oxlint)
  - G24: Playwright E2E Browser Suite
  - G25: Deterministic Replay
  - G26: Acceptance Runner Negative-Control Tests
  - G27: Artifact & Source SHA Integrity
  - G28: Documentation Completeness & Consistency
- Replaces ad-hoc string comparisons with `determine_release_verdict()`.

### 2.3 `backend/tests/conftest.py`
- Added `sys.path` resolution for `backend/` and `backend/tests/`.
- Added autouse session fixture `register_test_tmp_path_root` to register pytest `basetemp` as a trusted test root.

### 2.4 New & Updated Test Suites
- **`backend/tests/test_v17_7_database_safety.py`**:
  15 dedicated tests (`DBS-01` to `DBS-15`) verifying default test root, rejection of runtime/external paths, symlinks, traversals, URIs, env vars, sentinel DB byte preservation, worker isolation, and migration guards.
- **`backend/tests/test_v17_7_atr_provenance.py`**:
  20 dedicated tests (`ATR-01` to `ATR-20`) verifying raw ATR vs buffer separation, fail-closed volatility barriers, symbol/timeframe checks, unclosed/future candle exclusion, and ratio math.
- **`backend/tests/test_v17_7_adaptive_stop_loss.py`**:
  - `test_ce_16`: Updated to test concurrent real ARM via `orch._arm_paper_candidate()` across threads with barrier.
  - `test_ce_17`: Updated to verify strict path containment within `get_or_create_test_root()`.
  - `test_ce_19`: Updated to test `determine_release_verdict()` against failure mutations.
  - `test_ce_20`: Updated to verify artifact `source_sha` matches `git rev-parse HEAD`.
