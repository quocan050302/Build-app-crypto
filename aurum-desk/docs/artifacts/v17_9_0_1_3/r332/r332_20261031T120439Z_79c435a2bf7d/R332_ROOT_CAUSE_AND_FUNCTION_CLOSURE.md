# AURUM DESK V17.9.0.1.3 R3.3.2 — Root-Cause & Exact-Function Closure Matrix (`P0-1` through `P0-6`)

- **Run ID:** `r332_20261031T120439Z_79c435a2bf7d`
- **Branch:** `fix/v17-9-0-1-3-historical-parity-hardening`
- **Frozen Executable Code SHA (`FROZEN_EXECUTABLE_SHA`):** `79c435a2bf7d8fdbd7a30fa308023e6da70c0030`
- **Backtest Source SHA (`BACKTEST_SOURCE_SHA`):** `79c435a2bf7d8fdbd7a30fa308023e6da70c0030`
- **Pinned Historical Pre-Refactor Baseline SHA:** `c356d6551e13cd6af61cea5c3ab1f4aae1548af2`

## 1. Exact Root-Cause & Function-Level Closure Matrix

| Defect ID | Root Cause in R3.3.1 | Exact Repaired Functions | Negative & Fault-Injection Controls | Status |
|---|---|---|---|---|
| `P0-1` | `tools/run_v17_8_acceptance.py` overwrote `testcases_by_stem[f_stem] = r_tcs` and rewrote `s_res['code'] = 0` when an isolated retry passed after a primary shard failure. | `tools/run_v17_8_acceptance.py::_run_pytest_shard`, `_retry_failed_stem_isolated`, `main` | `R332-01`, `R332-03`, `R332-11`, `R332-19`, `R332-31` | `CLOSED_VERIFIED` |
| `P0-2` | `tools/run_v17_8_acceptance.py` deleted raw shard XMLs (`shard_xml.unlink(missing_ok=True)`) and regenerated `pytest_batch_<n>.xml` from post-retry `testcases_by_stem`. | `tools/run_v17_8_acceptance.py::_run_pytest_shard`, `_retry_failed_stem_isolated`, `main` | `R332-02`, `R332-04`, `R332-05`, `R332-06`, `R332-21` | `CLOSED_VERIFIED` |
| `P0-3` | Shard 4 (`test_repro_m1_default_list_isolates_active_run`) and Shard 7 (`test_red_030_live_wiring_cache_to_scheduler_trace`) failed in parallel shards due to uncleaned `PaperResearchRunRecord`/`OpportunityFunnelRecord` rows and orphaned `_ACTIVE_RECEIPTS` telemetry state from neighboring tests. | `backend/tests/test_v17_1_monitor_scope_and_focus.py`, `backend/tests/test_v16_18_red_reproductions.py`, `backend/tests/test_v16_19_red_reproductions.py`, `backend/tests/test_v16_21_production_flow.py`, `backend/tests/test_v16_21_red_reproductions.py` | `R332-15`, `R332-16`, `R332-17`, `R332-18` | `CLOSED_VERIFIED` |
| `P0-4` | `materialize_pytest_gate()` wrote `COMMAND: ... (cached from parallel batch)` and `EXIT_CODE: 0` into gate logs/sidecars for derived XML projections that did not spawn a subprocess. | `tools/run_v17_8_acceptance.py::materialize_pytest_gate`, `run_pytest_gate`; `tools/acceptance_verdict.py::independently_reconcile_junit_scopes` | `R332-12`, `R332-13`, `R332-20`, `R332-33` | `CLOSED_VERIFIED` |
| `P0-5` | `R331_WORKER_EXECUTIONS.jsonl` only inspected the 17 Phase-H groups and omitted the master acceptance runner's primary shard and retry worker attempts. | `tools/run_v17_8_acceptance.py::run_cmd`, `main`; `tools/acceptance_verdict.py::verify_r332_evidence_bundle` | `R332-07`, `R332-08`, `R332-09`, `R332-10`, `R332-14`, `R332-22`, `R332-34` | `CLOSED_VERIFIED` |
| `P0-6` | Independent verifier did not reconcile raw primary shard XMLs, retry XMLs, derived gate projections, and all 30 R3.3.2 mandatory deliverables fail-closed. | `tools/runtime_execution_logger.py::create_r332_run_context`, `verify_r332_pre_execution_manifest`, `finalize_r332_run_manifest`; `tools/acceptance_verdict.py::verify_r332_evidence_bundle` | `R332-23` through `R332-36` | `CLOSED_VERIFIED` |
