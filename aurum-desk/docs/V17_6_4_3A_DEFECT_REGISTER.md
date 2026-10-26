# AURUM V17.6.4.3a Defect Register

## Overview
This document tracks all defects identified in **Master Prompt V17.6.4.3a** and their resolution lifecycle.
Allowed Statuses: `OPEN`, `REPRODUCED`, `FIXED`, `VERIFIED`, `BLOCKED`.
Current Version: **V17.6.4.3a**

---

### Defect Register Table

| Defect ID | Description | Severity | File / Function | Status | Reproduction Test | Verification Suite |
|---|---|---|---|---|---|---|
| **AC43A-01** | Borrowed Session flush reported as COMMITTED | P0 | `backend/services/observation_interval_service.py` (`_persist_interval_record`) | VERIFIED | `test_v17_6_4_3a_commit_truth.py::test_ct02` | `backend/tests/test_v17_6_4_3a_commit_truth.py` (15/15 PASS) |
| **AC43A-02** | Pending interval acknowledged before real commit | P0 | `backend/services/observation_interval_service.py` (`persist_pending_intervals`) | VERIFIED | `test_v17_6_4_3a_commit_truth.py::test_ct05` | `backend/tests/test_v17_6_4_3a_commit_truth.py` (15/15 PASS) |
| **AC43A-03** | Typed GOOD evidence remains forgeable via dataclass | P0 | `backend/services/engine_observability_service.py` (`record_engine_heartbeat`) | VERIFIED | `test_v17_6_4_3a_authoritative_good.py::test_ag01` | `backend/tests/test_v17_6_4_3a_authoritative_good.py` (18/18 PASS) |
| **AC43A-04** | GOOD authorization not bound to canonical quote provenance | P0 | `backend/services/engine_observability_service.py` (`record_engine_heartbeat`) | VERIFIED | `test_v17_6_4_3a_authoritative_good.py::test_ag04` | `backend/tests/test_v17_6_4_3a_authoritative_good.py` (18/18 PASS) |
| **AC43A-05** | Backend discovery can PASS with nonzero pytest exit | P1 | `tools/run_v17_6_4_3a_acceptance.py` (`run_backend_discovery`) | VERIFIED | `test_v17_6_4_3a_discovery_integrity.py::test_dc03` | `backend/tests/test_v17_6_4_3a_discovery_integrity.py` (10/10 PASS) |
| **AC43A-06** | Unexpected skipped tests can PASS | P1 | `tools/run_v17_6_4_3a_acceptance.py` (`run_cmd`) | VERIFIED | `test_v17_6_4_3a_acceptance_integrity.py::test_ai05` | `backend/tests/test_v17_6_4_3a_acceptance_integrity.py` (16/16 PASS) |
| **AC43A-07** | Engine Health uses unscoped pending metrics | P1 | `backend/services/engine_observability_service.py` (`build_engine_health_snapshot`) | VERIFIED | `test_v17_6_4_3a_health_db_scoping.py::test_hs01_hs02` | `backend/tests/test_v17_6_4_3a_health_db_scoping.py` (9/9 PASS) |
| **AC43A-08** | Acceptance evidence not fully reconciled | P1 | `tools/run_v17_6_4_3a_acceptance.py` | VERIFIED | `test_v17_6_4_3a_acceptance_integrity.py` | `tools/run_v17_6_4_3a_acceptance.py` (32 Gates) |
| **AC43A-09** | Transaction tests encode incorrect expected behavior | P1 | `backend/tests/test_v17_6_4_3_transaction_boundaries.py` | VERIFIED | `test_v17_6_4_3_transaction_boundaries.py::test_tx01_tx02` | `backend/tests/test_v17_6_4_3_transaction_boundaries.py` (4/4 PASS) |
| **AC43A-10** | Source/runbook defect statuses disagree with actual results | P1 | `docs/V17_6_4_3A_DEFECT_REGISTER.md` | VERIFIED | Cross-Verification Audit | Defect Register and Documentation Suite Reconciled |

---

### Detailed Defect Records

#### AC43A-01: Borrowed Session Flush Reported as COMMITTED
- **Root Cause**: `ObservationIntervalService._persist_interval_record()` executed `target_db.flush()` on borrowed sessions (`close_session=False`) and returned `IntervalPersistenceResult(status="COMMITTED")`. A database `flush()` only sends SQL to the transaction buffer; it does not commit to disk or establish durable ACID boundaries.
- **Exact File / Function**: `backend/services/observation_interval_service.py` -> `ObservationIntervalService._persist_interval_record()`
- **Production Change**: Return `IntervalPersistenceResult(status="PENDING_COMMIT", interval_id=record.id)` on borrowed session flush. Verify that independent probe sessions cannot see uncommitted data and that `ALREADY_EXISTS` is never falsely returned for the caller session's own uncommitted insert.
- **Reproduction Test**: `test_v17_6_4_3a_commit_truth.py::test_ct02_borrowed_session_returns_pending_commit`
- **Passing Test**: `test_v17_6_4_3a_commit_truth.py` (All 15 tests pass)
- **Status**: VERIFIED

#### AC43A-02: Pending Interval Acknowledged Before Real Commit
- **Root Cause**: If a borrowed session reported `COMMITTED`, `persist_pending_intervals()` evicted the interval from `_pending_intervals`, advancing counters prematurely before outer transaction commitment.
- **Exact File / Function**: `backend/services/observation_interval_service.py` -> `ObservationIntervalService.persist_pending_intervals()`
- **Production Change**: Only evict intervals on confirmed `COMMITTED` (from owned transaction) or `ALREADY_EXISTS` (verified by independent readback). Retain intervals in `PENDING_COMMIT` until outer commit is acknowledged.
- **Reproduction Test**: `test_v17_6_4_3a_commit_truth.py::test_ct05_pending_interval_retained_until_acknowledgement`
- **Passing Test**: `test_v17_6_4_3a_commit_truth.py::test_ct05_pending_interval_retained_until_acknowledgement`
- **Status**: VERIFIED

#### AC43A-03: Typed GOOD Evidence Remains Forgeable
- **Root Cause**: `EngineObservabilityService.record_engine_heartbeat()` checked whether an `ObservationEvidenceQuality` instance had `quality="GOOD"`, `quote_valid=True`, `owner_verified=True`, etc. Any caller could instantiate this dataclass and bypass quote validation.
- **Exact File / Function**: `backend/services/engine_observability_service.py` -> `EngineObservabilityService.record_engine_heartbeat()`
- **Production Change**: Removed the typed object positive bypass entirely. Require that positive `GOOD` evidence originate strictly from actual canonical quote validation (`feed_data` processed via `resolve_observation_evidence_quality`).
- **Reproduction Test**: `test_v17_6_4_3a_authoritative_good.py::test_ag01_full_property_forged_dataclass_rejected`
- **Passing Test**: `test_v17_6_4_3a_authoritative_good.py` (All 18 tests pass)
- **Status**: VERIFIED

#### AC43A-04: GOOD Authorization Not Bound to Canonical Quote Provenance
- **Root Cause**: If `quote_source` was missing, `record_engine_heartbeat` defaulted `quote_source="WS"` if `worker_role` was `WATCHER_PROCESS` or `SCHEDULER`. A caller claiming a role could manufacture WebSocket quote provenance without a quote snapshot.
- **Exact File / Function**: `backend/services/engine_observability_service.py` -> `EngineObservabilityService.record_engine_heartbeat()`
- **Production Change**: Ceased automatic `quote_source="WS"` fabrication based on `worker_role`. Demand explicit canonical quote and transport metadata.
- **Reproduction Test**: `test_v17_6_4_3a_authoritative_good.py::test_ag04_forged_worker_role_source_rejected`
- **Passing Test**: `test_v17_6_4_3a_authoritative_good.py::test_ag04_forged_worker_role_source_rejected`
- **Status**: VERIFIED

#### AC43A-05: Backend Discovery Can PASS with Nonzero Pytest Exit
- **Root Cause**: In `tools/run_v17_6_4_3_acceptance.py`, `run_backend_discovery()` evaluated to `PASS` as long as `collected_count > 0`, ignoring nonzero exit codes (e.g. exit code 2 from syntax or collection errors).
- **Exact File / Function**: `tools/run_v17_6_4_3a_acceptance.py` -> `run_backend_discovery()`
- **Production Change**: Require `code == 0` AND `collected_count > 0` AND no collection error strings. Any collection failure or nonzero exit code immediately fails Gate 18.
- **Reproduction Test**: `test_v17_6_4_3a_discovery_integrity.py::test_dc03_exit_2_with_partial_collection_fails`
- **Passing Test**: `test_v17_6_4_3a_discovery_integrity.py` (All 10 tests pass)
- **Status**: VERIFIED

#### AC43A-06: Unexpected Skipped Tests Can PASS
- **Root Cause**: `run_cmd()` checked JUnit XML test failures and errors, but did not enforce zero unexpected skips for release gates.
- **Exact File / Function**: `tools/run_v17_6_4_3a_acceptance.py` -> `run_cmd()` and `parse_junit_xml()`
- **Production Change**: Parse testcase-level `<skipped>` elements, track unexpected skips, and reject release acceptance if unexpected skips occur in mandatory suites.
- **Reproduction Test**: `test_v17_6_4_3a_acceptance_integrity.py::test_ai05_unexpected_skipped_test`
- **Passing Test**: `test_v17_6_4_3a_acceptance_integrity.py` (All 16 tests pass)
- **Status**: VERIFIED

#### AC43A-07: Engine Health Uses Unscoped Pending Metrics
- **Root Cause**: `EngineObservabilityService.build_engine_health_snapshot()` called `observation_interval_service.get_pending_metrics(run_id=...)` without passing `db=db`, reading global pending state across all databases.
- **Exact File / Function**: `backend/services/engine_observability_service.py` -> `EngineObservabilityService.build_engine_health_snapshot()`
- **Production Change**: Pass `db=db` to `observation_interval_service.get_pending_metrics(db=db, run_id=...)`, scoping pending metrics strictly to the destination database.
- **Reproduction Test**: `test_v17_6_4_3a_health_db_scoping.py::test_hs01_hs02_database_a_failure_database_b_independent`
- **Passing Test**: `test_v17_6_4_3a_health_db_scoping.py` (All 9 tests pass)
- **Status**: VERIFIED

#### AC43A-08: Acceptance Evidence Not Fully Reconciled
- **Root Cause**: Prior release runners did not reconcile SHA, discovery exit codes, unexpected skips, and per-gate JUnit XML manifests into a unified verifiable release artifact.
- **Exact File / Function**: `tools/run_v17_6_4_3a_acceptance.py`
- **Production Change**: Implemented 32-gate master runner with full JUnit parsing, strict discovery, unexpected skip rejection, and SHA-256 artifact verification.
- **Reproduction Test**: `backend/tests/test_v17_6_4_3a_acceptance_integrity.py`
- **Passing Test**: `test_v17_6_4_3a_acceptance_integrity.py` (16/16 PASS)
- **Status**: VERIFIED

#### AC43A-09: Transaction Tests Encode Incorrect Expected Behavior
- **Root Cause**: Historical test `test_tx01_tx02_borrowed_session_flush_and_rollback` in `backend/tests/test_v17_6_4_3_transaction_boundaries.py` asserted `res.status == "COMMITTED"` for a borrowed session flush.
- **Exact File / Function**: `backend/tests/test_v17_6_4_3_transaction_boundaries.py` -> `test_tx01_tx02_borrowed_session_flush_and_rollback`
- **Production Change**: Corrected test assertion to `res.status == "PENDING_COMMIT"`, confirming rollback erases all traces from the database.
- **Reproduction Test**: `backend/tests/test_v17_6_4_3_transaction_boundaries.py::test_tx01_tx02_borrowed_session_flush_and_rollback`
- **Passing Test**: `backend/tests/test_v17_6_4_3_transaction_boundaries.py` (4/4 PASS)
- **Status**: VERIFIED

#### AC43A-10: Source/Runbook Defect Statuses Disagree with Actual Results
- **Root Cause**: Prior documentation listed open defects despite claiming full gate passage, creating contradictory release records.
- **Exact File / Function**: `docs/V17_6_4_3A_DEFECT_REGISTER.md`
- **Production Change**: Reconciled every defect with verified reproduction tests, verified code changes, and passing verification test suites.
- **Reproduction Test**: Audit cross-check across test suite and git tree.
- **Passing Test**: Reconciled defect register matching test suite outcomes.
- **Status**: VERIFIED
