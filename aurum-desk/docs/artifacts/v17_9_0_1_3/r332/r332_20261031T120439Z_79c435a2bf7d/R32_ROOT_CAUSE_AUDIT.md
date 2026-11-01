# AURUM DESK V17.9.0.1.3 R3.2 — ROOT CAUSE AUDIT

## 1. Executive Summary & Audit Context

- **Release Target:** AURUM Desk V17.9.0.1.3
- **Audit Phase:** R3.2 Single Clean Acceptance Run, Execution Provenance Closure, Bounded Async Regression & Exact-Source Backtest
- **Repository:** `quocan050302/Build-app-crypto`
- **Branch:** `fix/v17-9-0-1-3-historical-parity-hardening`
- **Auditor Role:** Principal Quantitative Trading Systems Engineer, Senior Python Backend Engineer, Independent Release Auditor

Following the R3.1 acceptance cycle, an independent forensic audit identified seven remaining evidence-integrity defects (P0-01 through P0-07) preventing authoritative release closure. This document provides the engineering analysis and root cause identification for each defect.

---

## 2. Forensic Defect Analysis (P0-01 to P0-07)

### 2.1 Blocker P0-01: Artifact Generation Derives Artifacts from Previous Run Directory Rather Than a Single Clean R3.2 Run
- **Defect ID:** DEF-179013-R32-01 (P0-01)
- **Component:** `tools/generate_v17_9_0_1_artifacts.py` (lines ~2996–3012)
- **Root Cause:**
  The artifact generation script contained hardcoded fallback logic:
  ```python
  if target_reports_dir is None:
      target_reports_dir = REPO_ROOT / "docs" / "artifacts" / "v17_9_0" / "run_v17_9_0_1793411200786_34380" / "reports"
  ```
  Instead of initiating a fresh, self-contained run directory under `docs/artifacts/v17_9_0_1_3/r32/<run_id>/`, the artifact engine fell back to an older acceptance run directory from V17.9.0. This caused downstream test aggregators and reports to read stale XML results and report historical rather than authentic current-run metrics.
- **Resolution:**
  Eradicate the fallback path entirely. Create an authoritative run directory `docs/artifacts/v17_9_0_1_3/r32/<run_id>/` with dedicated `reports/` and `logs/` subdirectories at execution start. Emit `R32_RUN_MANIFEST.json` binding all artifacts to `run_id`. Fail closed immediately if any required current-run data is missing.

---

### 2.2 Blocker P0-02: Runtime Input Captures Lack Authenticated Live Execution Provenance
- **Defect ID:** DEF-179013-R32-02 (P0-02)
- **Component:** `tools/generate_v17_9_0_1_artifacts.py`, `tools/runtime_execution_logger.py`
- **Root Cause:**
  Runtime input captures were constructed retrospectively inside `tools/generate_v17_9_0_1_artifacts.py` by assembling dictionaries after the execution cycle had already completed, rather than being observed and emitted at the actual execution boundaries. Consequently, there was no cryptographic or temporal proof that the captured inputs were the exact inputs passed to the production callables during execution.
- **Resolution:**
  Implement `RuntimeExecutionEventLogger` to observe callables directly. Emit real-time lifecycle events:
  1. `INVOCATION_STARTED` with unique execution ID, callable identity, argument hash, and monotonic start timestamp.
  2. `ACTUAL_INPUT_CAPTURED` immediately before entry with raw and canonical semantic hashes.
  3. `INVOCATION_COMPLETED` (or `INVOCATION_FAILED`) with duration, exit status, and output summary.
  Emit `R32_RUNTIME_EXECUTION_EVENTS.jsonl` and construct `R32_RUNTIME_INPUT_CAPTURES.jsonl` strictly from completed event sequences.

---

### 2.3 Blocker P0-03: JUnit Test Count Contradiction Across Artifacts
- **Defect ID:** DEF-179013-R32-03 (P0-03)
- **Component:** `tools/generate_v17_9_0_1_artifacts.py`, `tools/acceptance_verdict.py`
- **Root Cause:**
  Discrepancies existed across markdown audits and JSON reports regarding total JUnit test counts (e.g., 5,793 vs 5,931 vs 5,939). Investigation revealed that static markdown templates had retained numbers from older test configurations, while `aggregate_junit_xml_reports` accurately parsed the 93 XML report files produced by the test suite (yielding 5,793 executions, 4,398 unique test cases, 5,783 passed, 10 skipped, 0 failures).
- **Resolution:**
  Enforce dynamic derivation of test metrics across ALL artifacts directly from current-run raw XML files. Synchronize `R32_JUNIT_AGGREGATION.json`, `R32_ASYNC_PERFORMANCE_REPORT.json`, `R32_RUN_MANIFEST.json`, and `R32_FINAL_ACCEPTANCE_AUDIT.md` so that all reports cite the exact same counts.

---

### 2.4 Blocker P0-04: Negative Control Incomplete Coverage (N01–N148)
- **Defect ID:** DEF-179013-R32-04 (P0-04)
- **Component:** `backend/tests/test_v17_9_0_1_2_strict_eligibility_and_parity.py`
- **Root Cause:**
  Although the negative test functions `test_n146_missing_r31_mandatory_deliverable_fails_closed`, `test_n147_r31_exact_source_manifest_sha_mismatch_fails_closed`, and `test_n148_uncompleted_runtime_capture_invocation_fails_closed` were implemented and executed by pytest (149 total test items collected), they were not registered in the `checks` array of `run_all_v17_9_0_1_2_negative_acceptance_checks`. Consequently, the programmatic verification suite only reported on 145 checks instead of all 148 negative controls.
- **Resolution:**
  Register tests N146, N147, and N148 in the `checks` list of `run_all_v17_9_0_1_2_negative_acceptance_checks`. Verify that all 148 controls pass. Generate `R32_NEGATIVE_CONTROL_REGISTRY.json` and `R32_NEGATIVE_CONTROL_RESULTS.json` certifying 148/148 pass with 0 failures.

---

### 2.5 Blocker P0-05: Synthetic Fallback Defaults in Async Worker and Resource Telemetry
- **Defect ID:** DEF-179013-R32-05 (P0-05)
- **Component:** `tools/generate_v17_9_0_1_artifacts.py` (lines ~3063–3090)
- **Root Cause:**
  When concurrency and resource report files were not found in the run directory, the artifact script used synthetic hardcoded fallback defaults:
  - `conc_data.get("wall_clock_ms", 472878)`
  - `conc_data.get("max_workers", 10)`
  - `disk_data.get("peak_memory_bytes", 67108864)`
  - `disk_data.get("free_disk_bytes_postflight", 54408187904)`
  This created the appearance of measurement while potentially reporting synthetic constants.
- **Resolution:**
  Eradicate all synthetic fallback constants. Sample system resources directly using `psutil`, `shutil.disk_usage`, `os.cpu_count()`, and monotonic timers (`time.perf_counter()`). Fail closed or emit explicit `NOT_MEASURED` if data cannot be observed.

---

### 2.6 Blocker P0-06: Acceptance Verdict Relies on Legacy Artifact Locations Without Verifying Single-Run Manifest
- **Defect ID:** DEF-179013-R32-06 (P0-06)
- **Component:** `tools/acceptance_verdict.py`
- **Root Cause:**
  `verify_persisted_acceptance_artifacts` validated file names in `docs/v17_9_0_1_3/` independently without verifying that all files originated from the same coherent execution run. If files from different runs were mixed in the target directory, the verifier could validate artifacts produced at different times or against different commits.
- **Resolution:**
  Require `R32_RUN_MANIFEST.json` as an authoritative root in `verify_persisted_acceptance_artifacts`. Extract `manifest_run_id` and enforce that every JSON artifact carrying a `run_id` matches the manifest run ID. Reject mixed-run artifact sets fail-closed with `RUN_ID_MISMATCH`.

---

### 2.7 Blocker P0-07: Incomplete Execution Boundary Coverage in Runtime Verification
- **Defect ID:** DEF-179013-R32-07 (P0-07)
- **Component:** `tools/acceptance_verdict.py` (`verify_runtime_input_captures`)
- **Root Cause:**
  The validation logic in `verify_runtime_input_captures` only verified that `HISTORICAL_PRODUCTION` did not contain `"MarketShadow"` in its capture point. The other four execution boundaries (`SHARED_STRATEGY_EVALUATOR`, `PRODUCTION`, `MARKET_SHADOW`, `CHRONOLOGICAL_BACKTEST`) had no callable identity verification, allowing potential mock or wrapper substitution without detection.
- **Resolution:**
  Extend `verify_runtime_input_captures` to enforce strict production callable identities across all 5 boundaries:
  1. `HISTORICAL_PRODUCTION`: Reject `"marketshadow"` and current-tree mocks; verify canonical pre-refactor baseline callable.
  2. `SHARED_STRATEGY_EVALUATOR`: Enforce `SharedStrategyEvaluator.evaluate_decision`.
  3. `PRODUCTION`: Enforce `ScalpWatcherOrchestrator.evaluate_cycle`.
  4. `MARKET_SHADOW`: Enforce `MarketShadowService.evaluate_shadow_cycle`.
  5. `CHRONOLOGICAL_BACKTEST`: Enforce `v17_8_backtest_runner.execute_chronological_backtest_window_for_parity`.
  When events are supplied, verify that every capture record has corresponding `INVOCATION_STARTED`, `ACTUAL_INPUT_CAPTURED`, and `INVOCATION_COMPLETED` events.

---

## 3. Conclusion & Implementation Mandate

All seven defects are structural evidence-integrity blockers. Their resolution requires:
1. Registration of N146–N148 in the negative control suite.
2. Introduction of `RuntimeExecutionEventLogger` and event verification.
3. Creation of an immutable R3.2 single-run directory bound to `R32_RUN_MANIFEST.json`.
4. Elimination of all synthetic fallback defaults.
5. Strict cross-runtime callable verification.

