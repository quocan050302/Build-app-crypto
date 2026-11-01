# AURUM DESK V17.9.0.1.3 R3.3.1 — Root-Cause & Exact-Function Closure Matrix (`P0-A` through `P0-F`)

- **Run ID:** `r332_20261031T120439Z_79c435a2bf7d`
- **Branch:** `fix/v17-9-0-1-3-historical-parity-hardening`
- **Frozen Executable Code SHA (`FROZEN_EXECUTABLE_SHA`):** `79c435a2bf7d8fdbd7a30fa308023e6da70c0030`
- **Backtest Source SHA (`BACKTEST_SOURCE_SHA`):** `79c435a2bf7d8fdbd7a30fa308023e6da70c0030`
- **Pinned Historical Pre-Refactor Baseline SHA:** `c356d6551e13cd6af61cea5c3ab1f4aae1548af2`

## 1. Exact Root-Cause & Function-Level Closure Matrix

| Defect ID | Root Cause in R3.3 | Exact Repaired Functions | Negative & Fault-Injection Controls | Status |
|---|---|---|---|---|
| `P0-A` | `R33_RUN_MANIFEST.json` was materialized inside `generate_all_v17_9_0_1_artifacts()` after backtest/regression completed (`created_at > task_start`). | `tools/runtime_execution_logger.py::create_r331_run_context`, `verify_r331_pre_execution_manifest`, `finalize_r331_run_manifest`; `tools/acceptance_verdict.py::verify_pre_execution_run_manifest` | `R331-01`, `R331-02`, `R331-03`, `R331-04`, `R331-05` | `CLOSED_VERIFIED` |
| `P0-B` | `tools/generate_v17_9_0_1_artifacts.py::_measure_process_peak_memory_bytes()` multiplied `ru_maxrss * 1024` on macOS (`619,920 MB`) and fell back to `67108864` (`64 MiB`). | `tools/test_resource_guard.py::normalize_ru_maxrss_to_bytes`, `measure_process_memory_scopes`, `TestResourceGuard.build_cpu_memory_report`; `tools/acceptance_verdict.py::verify_resource_telemetry_semantics` | `R331-06`, `R331-07`, `R331-08`, `R331-09`, `R331-10` | `CLOSED_VERIFIED` |
| `P0-C` | `tools/run_v17_8_acceptance.py` materialized `g58_release_artifact_truth.xml` after `G87` and used `-k "... or test_n"` on `G100`, causing `5957/92` vs `5816/93` discrepancy. | `tools/run_v17_8_acceptance.py` (pre-G87 `g58` materialization + `G100` selector alignment); `tools/acceptance_verdict.py::independently_reconcile_junit_scopes` | `R331-11`, `R331-12`, `R331-13`, `R331-14`, `R331-15` | `CLOSED_VERIFIED` |
| `P0-D` | `tools/generate_v17_9_0_1_artifacts.py` fell back to `latest_run.json` and rewrote nested `run_id` / `tested_source_sha` fields on `acceptance_results.json`. | `tools/generate_v17_9_0_1_artifacts.py::copy_verified_current_run_artifacts`; `tools/acceptance_verdict.py::verify_unmodified_run_identity_tree` (`assert_nested_run_provenance`) | `R331-16`, `R331-17`, `R331-18`, `R331-19`, `R331-20` | `CLOSED_VERIFIED` |
| `P0-E` | `run_cmd` lacked a global semaphore & real child PID tracking, and `.xml.meta.json` `exit_code` was derived from XML counts instead of subprocess return codes. | `tools/run_v17_8_acceptance.py::run_cmd`; `tools/run_v17_9_async_regression.py::BoundedAsyncTestOrchestrator`; `tools/test_resource_guard.py::inspect_orphan_worker_processes` | `R331-21`, `R331-22`, `R331-23`, `R331-24`, `R331-25` | `CLOSED_VERIFIED` |
| `P0-F` | `verify_r33_evidence_bundle` used fail-open defaults (`w_row.get('exit_code', 0)`, `w_row.get('status') or 'PASS'`) and did not enforce complete hash coverage. | `tools/acceptance_verdict.py::verify_worker_execution_accounting`, `verify_mandatory_artifact_hash_coverage`, `verify_r331_evidence_bundle` | `R331-26`, `R331-27`, `R331-28`, `R331-29`, `R331-30` | `CLOSED_VERIFIED` |
