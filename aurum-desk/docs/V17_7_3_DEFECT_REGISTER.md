# AURUM DESK V17.7.3 — DEFECT CLOSURE REGISTER
**Audited Defect Inventory, Resolution Mapping & Machine Verification Proofs**  
**Repository:** `quocan050302/Build-app-crypto`  
**Branch:** `feat/v17-7-adaptive-structural-stop-loss`  
**Status:** `VERIFIED_FROM_SOURCE`

---

## 1. Defect Closure Summary

All 10 confirmed engineering findings identified in AURUM DESK V17.7.2 have been systematically resolved with negative and regression tests, source remediations, and verifiable runtime evidence.

| Defect ID | Severity | Status | Verification Mechanism | Verified Source File |
|---|---|---|---|---|
| `V173-P0-01` | CRITICAL | **CLOSED** | Database safety test suite (`DBS-01..15`) | `backend/database.py`, `backend/tests/test_v17_7_database_safety.py` |
| `V173-P0-02` | CRITICAL | **CLOSED** | Entire backend discovery (`4,092 tests`) | `tools/run_v17_7_acceptance.py` (Gate G20) |
| `V173-P1-01` | HIGH | **CLOSED** | Raw ATR vs weighted buffer (`ATR-01, 15`) | `backend/services/stop_placement_service.py` |
| `V173-P1-02` | HIGH | **CLOSED** | Authoritative ATR provenance (`ATR-02..20`) | `backend/services/stop_placement_types.py` |
| `V173-P1-03` | HIGH | **CLOSED** | Production ARM concurrency race (`test_ce_16`)| `backend/services/scalp_watcher_orchestrator.py` |
| `V173-P1-04` | HIGH | **CLOSED** | Standalone pure verdict engine (`test_ce_19`) | `tools/acceptance_verdict.py` |
| `V173-P1-05` | HIGH | **CLOSED** | Git commit SHA consistency check (`test_ce_20`)| `backend/tests/test_v17_7_adaptive_stop_loss.py` |
| `V173-P1-06` | HIGH | **CLOSED** | Dynamic regex & JSON counts parsing | `tools/run_v17_7_acceptance.py` |
| `V173-P1-07` | MEDIUM | **CLOSED** | Renamed gates to exact execution scope | `tools/run_v17_7_acceptance.py` |
| `V173-P1-08` | MEDIUM | **CLOSED** | De-duplicated unique vs gate-targeted tests | `docs/V17_7_3_FULL_BACKEND_REGRESSION.md` |

---

## 2. Detailed Defect Closure Reports

### V173-P0-01: Test Database Isolation Not Guaranteed for Arbitrary Explicit Paths
- **Severity:** CRITICAL (P0)
- **Root Cause:** Basename check (`"aurum_desk.db" not in path.name`) allowed arbitrary paths, symlinks, path traversals (`../`), and URI schemes to be targeted by destructive test fixtures.
- **Source Fix:**
  Implemented `assert_test_database_isolated()` in `backend/database.py` with strict canonical containment check `t_path.is_relative_to(root)` against verified test roots containing the `.aurum_test_owned` marker. Added `get_or_create_test_root()` and `register_trusted_test_root()`.
- **Verification Tests:**
  `DBS-01` through `DBS-15` in `backend/tests/test_v17_7_database_safety.py`. Verified that non-test paths, symlinks, traversals, and sentinel DBs are unconditionally rejected.
- **Status:** **CLOSED** (`VERIFIED_FROM_DATABASE_ISOLATION_TEST`)

---

### V173-P0-02: Full Backend Regression Was Selective
- **Severity:** CRITICAL (P0)
- **Root Cause:** Gate G16 ran `pytest backend/tests/test_v17_*.py -q`, excluding ~2,000 legacy and foundational tests.
- **Source Fix:**
  Replaced command with `python -m pytest backend/tests -q` under Gate G20, discovering and executing all 4,092 tests in `backend/tests/`.
- **Verification Tests:**
  Execution of Gate G20 in `tools/run_v17_7_acceptance.py`.
- **Status:** **CLOSED** (`VERIFIED_FROM_FULL_REGRESSION`)

---

### V173-P1-01: Weighted ATR Buffer Masquerading as Raw ATR
- **Severity:** HIGH (P1)
- **Root Cause:** `plan_structural_stop()` had a fallback accessing `buffer_result.components["atr_m1"]` (which equals `0.25 * atr_raw`), treating it as raw ATR and distorting distance-to-ATR ratios by 400%.
- **Source Fix:**
  Eliminated `components["atr_m1"]` fallback in `plan_structural_stop()`. Added `raw_atr_m1` and `raw_atr_m5` fields to `StopBufferResult` to explicitly carry unweighted metrics.
- **Verification Tests:**
  `ATR-01`, `ATR-02`, and `ATR-15` in `backend/tests/test_v17_7_atr_provenance.py`.
- **Status:** **CLOSED** (`VERIFIED_FROM_UNIT_TEST`)

---

### V173-P1-02: Structural ATR Eligibility Lacked Authoritative Provenance
- **Severity:** HIGH (P1)
- **Root Cause:** Volatility gates accepted unverified floats or bypassed checks without timestamp and candle cutoff validation.
- **Source Fix:**
  Extended `StopATRResult` with `symbol`, `source`, `source_candle_count`, `last_closed_candle_timestamp`, `calculated_as_of`, `decision_cutoff`. Enforced that unclosed or future bars are rejected and explicit `UNAVAILABLE` status cannot be overridden by scalars.
- **Verification Tests:**
  `ATR-03` through `ATR-14`, and `ATR-20` in `backend/tests/test_v17_7_atr_provenance.py`.
- **Status:** **CLOSED** (`VERIFIED_FROM_UNIT_TEST`)

---

### V173-P1-03: Real Concurrent ARM Idempotency Unverified
- **Severity:** HIGH (P1)
- **Root Cause:** Concurrency tests checked primary key collision directly on `PaperOrder`, rather than exercising `ScalpWatcherOrchestrator._arm_paper_candidate()`. Additionally, order writes were not committed inside the lock.
- **Source Fix:**
  Added `db.commit()` inside `_arm_paper_candidate()` within the `serialize_entry_mutation` lock before returning the order. Rewrote `test_ce_16` to launch two threads with `threading.Barrier(2)` executing `_arm_paper_candidate()` simultaneously.
- **Verification Tests:**
  `test_ce_16_concurrent_real_arm_barrier_single_order` in `backend/tests/test_v17_7_adaptive_stop_loss.py`.
- **Status:** **CLOSED** (`VERIFIED_FROM_CONCURRENT_RUNTIME_TEST`)

---

### V173-P1-04: Acceptance Verdict Logic Coupled & Untested
- **Severity:** HIGH (P1)
- **Root Cause:** Acceptance verdict was calculated inline with fragile logic; `test_ce_19` used mock dicts rather than production code.
- **Source Fix:**
  Extracted pure, fail-closed `determine_release_verdict()` and `MANDATORY_GATES_V17_7_3` manifest in `tools/acceptance_verdict.py`. Refactored `test_ce_19` to directly test the production verdict engine against missing, failed, skipped, and inconsistent inputs.
- **Verification Tests:**
  `test_ce_19_true_acceptance_runner_negative_controls` in `backend/tests/test_v17_7_adaptive_stop_loss.py`.
- **Status:** **CLOSED** (`VERIFIED_FROM_UNIT_TEST`)

---

### V173-P1-05: Tested Source SHA Integrity Unenforced
- **Severity:** HIGH (P1)
- **Root Cause:** `test_ce_20` checked hex length rather than verifying that the artifact's `source_sha` matches `git rev-parse HEAD`.
- **Source Fix:**
  Refactored `test_ce_20` to query the true git commit SHA and confirm that `results["source_sha"]` strictly matches HEAD.
- **Verification Tests:**
  `test_ce_20_acceptance_source_sha_and_artifact_integrity` in `backend/tests/test_v17_7_adaptive_stop_loss.py`.
- **Status:** **CLOSED** (`VERIFIED_FROM_SOURCE`)

---

### V173-P1-06: Test Collection & Deselected Counts Unparsed
- **Severity:** HIGH (P1)
- **Root Cause:** Previous test parsing relied on brittle string matching that overlooked collection errors and deselected counts.
- **Source Fix:**
  Implemented robust regex parser in `parse_pytest_counts()` and `parse_vitest_counts()` in `tools/run_v17_7_acceptance.py`.
- **Verification Tests:**
  Verified in all 28 acceptance gate executions.
- **Status:** **CLOSED** (`VERIFIED_FROM_SOURCE`)

---

### V173-P1-07: Gate Names Overstated Actual Scope
- **Severity:** MEDIUM (P1)
- **Root Cause:** Gates were named "FULL" or "COMPLETE" when only testing targeted subsets.
- **Source Fix:**
  Aligned gate names in `tools/run_v17_7_acceptance.py` to reflect exact test scopes (e.g. `G17_EXISTING_SL_DSL_CE_REGRESSION`, `G20_FULL_BACKEND_PYTEST`).
- **Verification Tests:**
  Manifest inspection in `tools/acceptance_verdict.py`.
- **Status:** **CLOSED** (`VERIFIED_FROM_SOURCE`)

---

### V173-P1-08: Test Count Inflation via Double-Counting
- **Severity:** MEDIUM (P1)
- **Root Cause:** Summing overlapping test suites created inflated total test numbers.
- **Source Fix:**
  Disaggregated unique backend test discovery (4,092 tests) from targeted gate validations (100 dedicated, 278 V17.5, 776 V17.6) and frontend suites (548 vitest, 3 playwright).
- **Verification Tests:**
  Documented in `docs/V17_7_3_FULL_BACKEND_REGRESSION.md` and release reports.
- **Status:** **CLOSED** (`VERIFIED_FROM_SOURCE`)
