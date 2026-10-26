# AURUM DESK — V17.6.4.2 DEFECT REGISTER
## Evidence Commit Integrity & Production Repair Inventory

**Document Version:** 1.0.0  
**Target Release:** V17.6.4.2  
**Baseline SHA:** `f033d4aa69aef03901ab672173b24c8df236b35e`  
**Branch:** `fix/v14-1-rr-flow`  
**Classification:** Critical Production Repair · Durable Evidence · Feed Authority · Honest Acceptance

---

### Defect Inventory (ECI-01 through ECI-14)

| ID | Priority | Defect Summary | Root Cause & Production Impact | Implementation Target | Verification Test Suite | Status |
|---|---|---|---|---|---|---|
| **ECI-01** | **P0** | Rotation/checkpoint discards interval before DB commit | When an interval rotated, it was extracted from active tracking and persistence was attempted. Transient DB failures (SQLite lock) permanently dropped the interval. | `ObservationIntervalService.record_heartbeat_event()` & `flush_active_intervals()` | `test_v17_6_4_2_pending_interval_commit.py` (PI01 - PI20) | **FIXED** |
| **ECI-02** | **P0** | Feed can become GOOD without authoritative freshness | `resolve_observation_evidence_quality()` allowed `None` quote ages, missing timestamps, and crossed books to evaluate as `GOOD`. | `engine_observability_service.resolve_observation_evidence_quality()` | `test_v17_6_4_2_quote_authority.py` (QA01 - QA18) | **FIXED** |
| **ECI-03** | **P0** | Dashboard defaults persistence to COMMITTED | `build_observation_dashboard_snapshot()` defaulted persistence to `COMMITTED` without verifying database rows, concealing uncommitted or degraded state. | `engine_observability_service.build_observation_dashboard_snapshot()` & UI | `test_v17_6_4_2_persistence_status.py` (PS01 - PS12), `v17_6_4_2_observation_status.test.tsx` (OS01 - OS07) | **FIXED** |
| **ECI-04** | **P0** | Acceptance `--skip-e2e` can produce ACCEPTED | In `run_v17_6_4_1_acceptance.py`, `--skip-e2e` set G26 to `PASS` with `SKIPPED_BY_FLAG`, allowing unexercised browser UI to achieve release acceptance. | `tools/run_v17_6_4_2_acceptance.py` | `test_v17_6_4_2_acceptance_integrity.py` (AC02), `test_v17_6_4_1_acceptance_runner.py` (AC13) | **FIXED** |
| **ECI-05** | **P0** | Acceptance `--allow-dirty` can produce ACCEPTED | Passing `--allow-dirty` permitted dirty worktrees with uncommitted changes to receive `ACCEPTED`. | `tools/run_v17_6_4_2_acceptance.py` | `test_v17_6_4_2_acceptance_integrity.py` (AC03, AC16) | **FIXED** |
| **ECI-06** | **P1** | Latest persisted cycle can fall back to uncommitted RAM | `get_latest_persisted_cycle()` fell back to `get_latest_cycle_diagnostic()` when DB row was missing, violating contract. | `EngineObservabilityService.get_latest_persisted_cycle()` | `test_v17_6_4_2_cycle_commit_truth.py` (CT07, CT08, CT09) | **FIXED** |
| **ECI-07** | **P1** | Cycle persistence reports COMMITTED after flush only | Calling `record_evaluation_cycle()` with a borrowed session reported `COMMITTED` immediately after `flush()`, before actual transaction commit. | `EngineObservabilityService.record_evaluation_cycle()` | `test_v17_6_4_2_cycle_commit_truth.py` (CT01, CT04, CT11, CT12) | **FIXED** |
| **ECI-08** | **P1** | Feed GOOD can be bypassed by metadata flags | `record_engine_heartbeat()` trusted metadata flags (`coverage_quality: "GOOD"`) without executing strict quote authority checks. | `EngineObservabilityService.record_engine_heartbeat()` | `test_v17_6_4_2_metadata_good_bypass.py` (MB01 - MB03) | **FIXED** |
| **ECI-09** | **P1** | Existing integration tests do not exercise actual watcher | Previous tests called telemetry service directly instead of invoking actual `ScalpWatcherOrchestrator.evaluate_cycle()`. | `backend/tests/test_v17_6_4_2_watcher_integration.py` | `test_v17_6_4_2_watcher_integration.py` (WI01 - WI07) | **FIXED** |
| **ECI-10** | **P1** | Strategy parity gate does not compare behavior | Previous acceptance runner only checked static symbol strings in source files without verifying numerical/behavioral parity against fixtures. | `backend/tests/test_v17_6_4_2_paper_parity.py` & G28 | `test_v17_6_4_2_paper_parity.py` (SP01 - SP06) | **FIXED** |
| **ECI-11** | **P1** | Live-order gate relies on superficial text scanning | Previous G27 gate only regex-scanned files for `LIVE_EXCHANGE_ORDER_EXECUTION = True` without executing runtime boundary checks. | `backend/tests/test_v17_6_4_2_paper_parity.py` & G29 | `test_v17_6_4_2_paper_parity.py` (ZO01 - ZO05) | **FIXED** |
| **ECI-12** | **P1** | Interval idempotency identity may be incomplete | `_persist_interval_record` filtered only by `(run_id, symbol, scope, start_ms, end_ms)`, omitting worker, origin, and quality dimensions. | `ObservationIntervalService._make_pending_key()` & persistence filter | `test_v17_6_4_2_pending_interval_commit.py` (PI11, PI12) | **FIXED** |
| **ECI-13** | **P1** | Error/retry path may leave inconsistent in-memory state | Uncommitted intervals, pending queues, and flush counters desynchronized on retry after transient database lock. | `ObservationIntervalService.persist_pending_intervals()` | `test_v17_6_4_2_pending_interval_commit.py` (PI02, PI13, PI14) | **FIXED** |
| **ECI-14** | **P1** | Acceptance evidence and tested SHA not fully reconciled | Acceptance report did not verify source SHA match between pre and post run, require clean worktree, or compute artifact sha256 checksums. | `tools/run_v17_6_4_2_acceptance.py` & G31 | `test_v17_6_4_2_acceptance_integrity.py` (AC01, AC10, AC11, AC15) | **FIXED** |

---

### Verification Summary

- **Total Defects Identified:** 14 (ECI-01 through ECI-14)
- **Total Defects Fixed:** 14 (100%)
- **New Unit & Integration Tests Created:** 106 tests across 8 test suites:
  1. `test_v17_6_4_2_pending_interval_commit.py`: 20 tests (PI01 - PI20) -> **ALL PASS**
  2. `test_v17_6_4_2_quote_authority.py`: 18 tests (QA01 - QA18) -> **ALL PASS**
  3. `test_v17_6_4_2_metadata_good_bypass.py`: 3 tests (MB01 - MB03) -> **ALL PASS**
  4. `test_v17_6_4_2_cycle_commit_truth.py`: 12 tests (CT01 - CT12) -> **ALL PASS**
  5. `test_v17_6_4_2_persistence_status.py`: 12 tests (PS01 - PS12) -> **ALL PASS**
  6. `test_v17_6_4_2_watcher_integration.py`: 7 tests (WI01 - WI07) -> **ALL PASS**
  7. `test_v17_6_4_2_paper_parity.py`: 11 tests (SP01 - SP06, ZO01 - ZO05) -> **ALL PASS**
  8. `test_v17_6_4_2_acceptance_integrity.py`: 16 tests (AC01 - AC16) -> **ALL PASS**
  9. `frontend/src/__tests__/v17_6_4_2_observation_status.test.tsx`: 7 tests (OS01 - OS07) -> **ALL PASS**
- **Existing Regression Test Suite:** 161 backend tests + 541 frontend tests -> **ALL PASS**
