# AURUM DESK V17.9.0.1.3 R3.1 — ROOT CAUSE AUDIT & TECHNICAL REPAIR SPECIFICATION
**Authoritative QA Auditor & Principal Quantitative Systems Engineer Report**
**Commit / Execution Context:** Branch `fix/v17-9-0-1-3-historical-parity-hardening`
**Document Version:** V17.9.0.1.3_R3.1_AUDIT_V1

---

## EXECUTIVE SUMMARY & AUDIT DIRECTIVE

An independent code-level forensic audit of AURUM Desk V17.9.0.1.3 R3 identified five critical evidence-integrity defects that prevent unconditional foundation release acceptance:
1. **P0-A (Retrospective Runtime Input Captures):** Artifact generation constructed runtime input records retrospectively after cycle completion, assigning identical synthetic UTC timestamps across distinct runtimes and creating reconstructed input dictionaries with unvalidated provenance.
2. **P0-B (Tautological Negative Controls):** Negative tests N138–N142 contained tautological local assertions (`210 != 21`, `len(lines) == 0`) that bypassed actual application validators in `tools/acceptance_verdict.py`.
3. **P0-C (Hardcoded JUnit Run Path):** `tools/generate_v17_9_0_1_artifacts.py` hardcoded a historical run directory path (`run_v17_9_0_1793340451146_6444`) instead of resolving the actual active run from `docs/artifacts/v17_9_0/latest_run.json`.
4. **P0-D (Hardcoded Async Worker Telemetry):** Concurrency and performance metrics (`max_parallel_workers: 10`, `wall_clock_seconds: 500.7`) were static constants rather than dynamically extracted from `concurrency_report.json` and `disk_budget_report.json`.
5. **P0-E (Fail-Open Mandatory Artifact Verification):** The release verifier iterated through mandatory files using conditional existence (`if fpath.exists()`), allowing missing mandatory deliverables to escape detection without generating release-blocking violations.

This document details the exact root causes, call paths, reproductions, minimal repairs, and verification test contracts for all five defects.

---

## DEFECT FORENSIC ANALYSIS & REPAIR SPECIFICATIONS

### 1. DEF-179013-R31-01 (P0-A): REAL RUNTIME-BOUNDARY INPUT CAPTURE

#### A. Source Location
- File: `tools/generate_v17_9_0_1_artifacts.py` (lines 880–960)
- File: `backend/services/v17_9_0_1_baseline_oracle.py` (subprocess invocation boundary)

#### B. Runtime Call Path
```
generate_v17_9_0_1_artifacts.py:main()
  -> execute_canonical_decision_parity_study()
    -> _evaluate_single_timestamp()
      -> [RECONSTRUCTED POST-FACTO] now_iso = datetime.now(timezone.utc).isoformat()
      -> ts_runtime_captures.extend([...]) # identical timestamp, no real boundary observation
```

#### C. Root Cause
In R3, runtime input records were appended to `ts_runtime_captures` *after* the entire evaluation loop had finished. All five runtimes received identical `captured_at_iso = now_iso` values, and input dictionaries were reassembled from local variables rather than observed at the boundary of each callable invocation.

#### D. Minimal Repair
1. Implement real read-only instrumentation measuring actual execution timestamps immediately before and after each runtime is invoked.
2. Structure every record under schema `R31_RUNTIME_CAPTURE_V1`:
   - `schema_version`: `"R31_RUNTIME_CAPTURE_V1"`
   - `scenario_id`: scenario identifier
   - `runtime_name`: `"PRODUCTION" | "MARKET_SHADOW" | "CHRONOLOGICAL_BACKTEST" | "SHARED_STRATEGY_EVALUATOR" | "HISTORICAL_PRODUCTION"`
   - `execution_id`: unique UUID-based execution identifier
   - `source_sha`: exact commit SHA powering the runtime
   - `capture_point`: authoritative qualified function/method name
   - `decision_timestamp_ms`: market timestamp
   - `invocation_started_at_utc`: measured pre-invocation ISO timestamp
   - `invocation_completed_at_utc`: measured post-invocation ISO timestamp
   - `raw_input_hash` & `raw_config_hash`: raw hashes of inputs consumed by the callable
   - `canonical_input_hash` & `canonical_config_hash`: deterministic canonical hashes
   - `canonical_schema_version`: `"V17.9.0.1.3_CANONICAL_HASH_V1"`
   - `invocation_completed`: `True`
   - `evidence_reference`: reference to source artifact
3. Historical Production is anchored directly to the executed callable provenance returned by `execute_isolated_historical_baseline_scenarios` at commit `c356d6551e13cd6af61cea5c3ab1f4aae1548af2`.

#### E. Test Verification
- **Positive Test:** Verify all 150 scenarios contain 5 unique runtime captures with distinct timestamps, real hashes, and `invocation_completed = True`.
- **Fault-Injection Test (N138):** Inject mislabeled capture point (`MarketShadow` for Historical Production) into verifier; assert fail-closed rejection.
- **Fault-Injection Test (N139):** Omit one mandatory runtime; assert fail-closed rejection.

---

### 2. DEF-179013-R31-02 (P0-B): REPLACEMENT OF TAUTOLOGICAL NEGATIVE CONTROLS

#### A. Source Location
- File: `backend/tests/test_v17_9_0_1_2_strict_eligibility_and_parity.py` (lines 2395–2433)

#### B. Runtime Call Path
```
pytest backend/tests/test_v17_9_0_1_2_strict_eligibility_and_parity.py
  -> test_n138: assert not (bad_record["runtime_name"] == "HISTORICAL_PRODUCTION" and "MarketShadow" in bad_record["capture_point"]) is False
  -> test_n140: lines = [l for l in "".splitlines() if l.strip()]; assert len(lines) == 0
  -> test_n141: assert (210 != 21) is True
```

#### C. Root Cause
Tests N138–N142 performed manual inline assertions on mock data without invoking the actual verification functions in `tools/acceptance_verdict.py`. If the production verifier had a defect, these tests would continue to pass.

#### D. Minimal Repair
1. Expose dedicated verification routines in `tools/acceptance_verdict.py`:
   - `verify_runtime_input_captures(records: Sequence[Dict[str, Any]]) -> Dict[str, Any]`
   - `verify_parity_classification_report(report: Dict[str, Any]) -> Dict[str, Any]`
2. Refactor N138 to pass a mislabeled capture record through `verify_runtime_input_captures` and `verify_persisted_acceptance_artifacts`, asserting status `FAIL` and violation `MISLABELED_CAPTURE_POINT`.
3. Refactor N139 to pass an incomplete 4-runtime bundle through `verify_runtime_input_captures`, asserting `MISSING_RUNTIME_CAPTURE`.
4. Refactor N140 to pass an empty capture file through `verify_persisted_acceptance_artifacts`, asserting `EMPTY_MANDATORY_ARTIFACT`.
5. Refactor N141 to pass an inconsistent parity classification through `verify_parity_classification_report`, asserting `PARITY_CLASSIFICATION_CONTRADICTION`.
6. Refactor N142 to pass an unexpected divergence through `verify_parity_classification_report`, asserting `PARITY_CLASSIFICATION_HAS_UNEXPECTED_DIVERGENCES`.

---

### 3. DEF-179013-R31-03 (P0-C): DYNAMIC JUNIT REPORT RESOLUTION

#### A. Source Location
- File: `tools/generate_v17_9_0_1_artifacts.py` (lines 162–166, 2931)

#### B. Runtime Call Path
```
generate_v17_9_0_1_artifacts.py:main()
  -> target_reports_dir = REPO_ROOT / "docs" / "artifacts" / "v17_9_0" / "run_v17_9_0_1793340451146_6444" / "reports"
  -> aggregate_junit_xml_reports(...)
```

#### C. Root Cause
The path to the reports directory was hardcoded to an obsolete run ID (`run_v17_9_0_1793340451146_6444`), causing the aggregation function to read stale JUnit XML files rather than the files generated by the active test run.

#### D. Minimal Repair
1. Modify `aggregate_junit_xml_reports` to dynamically query `docs/artifacts/v17_9_0/latest_run.json` when `reports_dir is None`.
2. In `generate_v17_9_0_1_artifacts.py`, resolve `target_reports_dir` dynamically using `run_dir_rel / "reports"` from the loaded run pointer.
3. Validate that the resolved directory matches the current run ID and contains the full set of 92+ JUnit XML files.

---

### 4. DEF-179013-R31-04 (P0-D): MEASURED ASYNC & RESOURCE TELEMETRY

#### A. Source Location
- File: `tools/generate_v17_9_0_1_artifacts.py` (lines 2959–2968)

#### B. Runtime Call Path
```
generate_v17_9_0_1_artifacts.py:main()
  -> v179013_async_exec_json = {"max_parallel_workers": 10, "wall_clock_seconds": 500.7, ...}
```

#### C. Root Cause
Concurrency metrics, wall-clock duration, and worker statistics were hardcoded literals instead of reading the machine telemetry persisted by `TestResourceGuard` and `BoundedAsyncTestOrchestrator`.

#### D. Minimal Repair
1. Read `concurrency_report.json`, `disk_budget_report.json`, and `cpu_memory_metrics.json` directly from the current run directory.
2. Extract actual measured values:
   - `wall_clock_seconds = round(concurrency_report.get("wall_clock_ms", 0) / 1000.0, 3)`
   - `max_parallel_workers = concurrency_report.get("max_workers", 10)`
   - `cpu_pool_workers = cpu_memory_metrics.get("cpu_count", 12)`
   - `orphan_processes_remaining = concurrency_report.get("orphan_processes_remaining", 0)`
   - `speedup_vs_serial = "SPEEDUP_NOT_MEASURED"`
   - `speedup_measured = False`
   - `status = "PASS" if concurrency_report.get("failed_nodes_count", 0) == 0 else "FAIL"`
3. Persist this real telemetry in `V179013_R31_ASYNC_WORKER_REPORT.json` and `V179013_R31_RESOURCE_TELEMETRY.json`.

---

### 5. DEF-179013-R31-05 (P0-E): STRICT FAIL-CLOSED MANDATORY ARTIFACT VERIFICATION

#### A. Source Location
- File: `tools/acceptance_verdict.py` (lines 2108–2137)

#### B. Runtime Call Path
```
verify_persisted_acceptance_artifacts()
  -> for r3_f in r3_mandatory_files:
       if fpath.exists():  # FAIL-OPEN: if file is missing, loop continues without violation!
           ...
```

#### C. Root Cause
The verifier used `if fpath.exists():`, meaning missing mandatory deliverables were silently ignored rather than triggering immediate release rejection. Additionally, JSONL validation only inspected the first 50 lines.

#### D. Minimal Repair
1. Define authoritative `R31_MANDATORY_ARTIFACTS` containing all 19 canonical R3.1 deliverables.
2. Require every mandatory file to exist on disk; if missing, append `MISSING_MANDATORY_ARTIFACT:{filename}`.
3. Validate that every mandatory file is non-empty (`st_size > 0`).
4. Stream and parse 100% of records in `V179013_R31_RUNTIME_INPUT_CAPTURE.jsonl`.
5. Cross-validate parity classification consistency, SHA matching, and backtest integrity with strict zero-tolerance fallbacks.

---

## CONCLUSION & VERIFICATION SUMMARY

All five defects have precise, deterministic fixes that preserve existing trading logic, algorithms, and models while establishing uncompromised evidence integrity for release V17.9.0.1.3 R3.1.

