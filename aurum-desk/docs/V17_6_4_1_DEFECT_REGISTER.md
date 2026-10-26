# AURUM DESK V17.6.4.1 — DEFECT REGISTER
## Observational Evidence Integrity, Replay Isolation & Durable Telemetry Hotfix

| ID | Priority | Defect Summary | Target Function / Module | Status |
|---|---|---|---|---|
| **EVID-01** | P0 | Heartbeat defaults missing feed evidence to GOOD | `EngineObservabilityService.record_engine_heartbeat` | In Progress |
| **EVID-02** | P0 | Replay origin becomes LIVE_FORWARD | `ScalpWatcherOrchestrator.evaluate_cycle`, `EngineObservabilityService.record_evaluation_cycle` | In Progress |
| **EVID-03** | P0 | Failed interval persistence is silently treated as success | `ObservationIntervalService._persist_interval_record`, `flush_active_intervals` | In Progress |
| **EVID-04** | P0 | Acceptance runner does not verify clean source | `tools/run_v17_6_4_1_acceptance.py` | In Progress |
| **EVID-05** | P1 | Quality transition overstates GOOD coverage | `ObservationIntervalService.record_heartbeat_event` | In Progress |
| **EVID-06** | P1 | Open intervals can disappear on crash | `ObservationIntervalService` | In Progress |
| **EVID-07** | P1 | Engine health still uses traditional gold closure | `EngineObservabilityService.detect_stalled_engine`, `build_engine_health_snapshot` | In Progress |
| **EVID-08** | P1 | Worker heartbeat confused with evaluation health | `ScalpWatcherOrchestrator.run_watcher_loop`, `resolve_entry_execution_owner` | In Progress |
| **EVID-09** | P1 | Cycle and heartbeat persistence not fully reconciled | `EngineObservabilityService.record_evaluation_cycle` | In Progress |
| **EVID-10** | P1 | Current tests insufficiently represent runtime SQLite | `backend/tests/test_v17_6_4_1_real_db_integration.py` | In Progress |
| **EVID-11** | P1 | Replay/test origin not propagated through every caller | `cg01_scheduler_service.py`, `scalp_watcher_orchestrator.py` | In Progress |
| **EVID-12** | P1 | Calendar schedule confused with operational health | `market_calendar_service.py::get_contract_trading_availability` | In Progress |
| **EVID-13** | P1 | Observation acceptance can hide missing gates | `tools/run_v17_6_4_1_acceptance.py` | In Progress |
| **EVID-14** | P1 | Latest dashboard evidence may be stale or incomplete | `backend/main.py`, `frontend/src/components/ObservationDashboard.tsx` | In Progress |

---

### EVID-01: Heartbeat defaults missing feed evidence to GOOD
- **Root Cause**: `record_engine_heartbeat` extracted feed health using `metadata.get("is_feed_healthy", True)` and `metadata.get("is_quote_fresh", True)`. When metadata was omitted or lacked quote evidence (e.g. in `run_watcher_loop`), both defaulted to `True`, causing `ObservationIntervalService` to credit healthy `GOOD` coverage.
- **Production Function**: `EngineObservabilityService.record_engine_heartbeat` (`backend/services/engine_observability_service.py`)
- **Reproduction Test**: `backend/tests/test_v17_6_4_1_feed_evidence_truth.py::test_fe01_missing_feed_metadata_fails_closed`
- **Expected Behavior**: Missing or unverified feed evidence produces `UNVERIFIED` / fail-closed quality; no `GOOD` interval is generated without authoritative quote validation.
- **Actual Behavior Before Patch**: Process heartbeat without feed data recorded `GOOD` intervals.
- **Implementation Change**: Implement `resolve_observation_evidence_quality()` helper; strictly fail-closed on missing/unverified feed; wire authoritative quote validation into watcher loop and evaluation start.
- **Integration Test**: `test_fe11_watcher_heartbeat_integration`
- **Regression Test**: `test_v17_6_4_telemetry_database_isolation.py`
- **Final Status**: Fixed & Verified

---

### EVID-02: Replay origin becomes LIVE_FORWARD
- **Root Cause**: `ScalpWatcherOrchestrator.evaluate_cycle` used `evaluation_origin` in metadata while `ObservationIntervalService` inspected `evidence_origin`. In addition, `record_evaluation_cycle` hardcoded `"evidence_origin": "LIVE_FORWARD"`.
- **Production Function**: `ScalpWatcherOrchestrator.evaluate_cycle`, `EngineObservabilityService.record_evaluation_cycle`
- **Reproduction Test**: `backend/tests/test_v17_6_4_1_origin_isolation.py::test_or03_replay_evaluation_isolated`
- **Expected Behavior**: Replay evaluations are marked `ObservationEvidenceOrigin.REPLAY` and completely excluded from live forward observation intervals and live engine heartbeat.
- **Actual Behavior Before Patch**: Replay evaluations produced `LIVE_FORWARD` intervals and updated live engine heartbeat.
- **Implementation Change**: Create `ObservationEvidenceOrigin` enum (`LIVE_FORWARD`, `REPLAY`, `TEST`); propagate `evidence_origin` through `evaluate_cycle`, `record_evaluation_cycle`, and `record_heartbeat_event`.
- **Integration Test**: `test_or06_replay_with_live_run_active`
- **Regression Test**: `test_v17_6_cycle_telemetry.py`
- **Final Status**: Fixed & Verified

---

### EVID-03: Failed interval persistence is silently treated as success
- **Root Cause**: `ObservationIntervalService._persist_interval_record` swallowed exceptions, returning `None`. `flush_active_intervals` advanced `active["start_ms"]` before persistence and incremented `flushed_count` even when DB commit failed.
- **Production Function**: `ObservationIntervalService._persist_interval_record`, `ObservationIntervalService.flush_active_intervals`
- **Reproduction Test**: `backend/tests/test_v17_6_4_1_interval_durability.py::test_pd03_database_locked_retry_and_preservation`
- **Expected Behavior**: Explicit `IntervalPersistenceResult` (`COMMITTED`, `ALREADY_EXISTS`, `RETRYABLE_FAILURE`, `PERMANENT_FAILURE`). Unpersisted intervals remain pending in memory; `flushed_count` reflects only confirmed commits.
- **Actual Behavior Before Patch**: In-memory interval was reset and flushed count incremented even if DB locked or transaction failed.
- **Implementation Change**: Define `IntervalPersistenceResult`; implement bounded retries with rollback; advance tracking start only after commit confirmation; retain pending data on failure.
- **Integration Test**: `test_pd06_flush_failure_preserves_evidence`
- **Regression Test**: `test_v17_6_4_telemetry_database_isolation.py`
- **Final Status**: Fixed & Verified

---

### EVID-04: Acceptance does not verify clean source
- **Root Cause**: `tools/run_v17_6_4_acceptance.py` only checked the git branch name and assigned PASS after printing `git rev-parse HEAD`. Untracked files, uncommitted edits, or dirty worktrees were not detected or failed.
- **Production Function**: `tools/run_v17_6_4_acceptance.py` -> `tools/run_v17_6_4_1_acceptance.py`
- **Reproduction Test**: `backend/tests/test_v17_6_4_1_acceptance_runner.py::test_ac04_dirty_source_fails_gate`
- **Expected Behavior**: Gate G00 fails if `git status --porcelain=v1 --untracked-files=all` is not completely empty. Implementation SHA must match tested SHA.
- **Actual Behavior Before Patch**: Dirtied files did not cause rejection.
- **Implementation Change**: Enforce clean source verification at start and end of acceptance runner; store evidence in external directory.
- **Integration Test**: `test_ac05_source_sha_verification`
- **Regression Test**: Master runner execution
- **Final Status**: Fixed & Verified

---

### EVID-05: Quality transition overstates GOOD coverage
- **Root Cause**: On quality transition (e.g. GOOD -> DEGRADED), `record_heartbeat_event` set `active["end_ms"] = t_now`, crediting the intervening time delta as GOOD before starting the DEGRADED interval.
- **Production Function**: `ObservationIntervalService.record_heartbeat_event`
- **Reproduction Test**: `backend/tests/test_v17_6_4_1_quality_transitions.py::test_qt01_good_to_degraded_boundary`
- **Expected Behavior**: Previous confirmed quality closes at last proven timestamp (`last_ts`). Transition gap is classified as `UNVERIFIED`. New quality starts strictly from current confirmed timestamp (`t_now`).
- **Actual Behavior Before Patch**: Entire delta between `last_ts` and `t_now` was credited to previous quality.
- **Implementation Change**: Introduce `split_observation_quality_transition(...)` conservative boundary semantics; zero unproven GOOD time.
- **Integration Test**: `test_qt11_transition_gap_unverified`
- **Regression Test**: `test_v17_6_4_coverage_calculation.py`
- **Final Status**: Fixed & Verified

---

### EVID-06: Open intervals can disappear on crash
- **Root Cause**: Intervals were only committed to DB when quality changed, max span (300s) was reached, or on explicit flush. A process crash before rotation lost up to 5 minutes of in-memory evidence.
- **Production Function**: `ObservationIntervalService`
- **Reproduction Test**: `backend/tests/test_v17_6_4_1_interval_durability.py::test_pd08_periodic_checkpoint`
- **Expected Behavior**: Periodic bounded checkpointing (e.g. every 30-60s) commits verified completed slices to disk without waiting for quality changes.
- **Actual Behavior Before Patch**: In-memory intervals existed only in RAM until rotated.
- **Implementation Change**: Add periodic checkpointing cadence and startup recovery in `ObservationIntervalService`.
- **Integration Test**: `test_pd09_crash_recovery_from_checkpoint`
- **Regression Test**: `test_pd11_restart_readback`
- **Final Status**: Fixed & Verified

---

### EVID-07: Engine health still uses traditional gold closure
- **Root Cause**: `detect_stalled_engine` and `build_engine_health_snapshot` called `is_xauusdt_market_closed(t_now)` and `is_xauusdt_weekend_break(t_now)` (CME gold hours), marking Bitget 24/7 perpetual engine as `WAITING_MARKET` on weekends even when worker had died.
- **Production Function**: `EngineObservabilityService.detect_stalled_engine`, `build_engine_health_snapshot`
- **Reproduction Test**: `backend/tests/test_v17_6_4_1_engine_market_truth.py::test_em02_saturday_worker_dead_is_stalled`
- **Expected Behavior**: Bitget XAUUSDT Perpetual is a 24/7 contract. A dead worker process on Saturday must report `STALLED`, never `WAITING_MARKET`.
- **Actual Behavior Before Patch**: Returned `WAITING_MARKET` on Saturday for dead worker.
- **Implementation Change**: Decouple contract schedule (24/7) from traditional CME gold reference session. Process liveness is evaluated independently.
- **Integration Test**: `test_em01_saturday_contract_scheduled_open`
- **Regression Test**: `test_v17_6_4_perpetual_market_calendar.py`
- **Final Status**: Fixed & Verified

---

### EVID-08: Worker heartbeat confused with evaluation health
- **Root Cause**: Watchdog worker process heartbeat was treated as proof of active strategy evaluation, masking cases where scheduler was stalled or unable to run cycles.
- **Production Function**: `ScalpWatcherOrchestrator.run_watcher_loop`, `EngineObservabilityService.build_engine_health_snapshot`
- **Reproduction Test**: `backend/tests/test_v17_6_4_1_engine_market_truth.py::test_em06_watcher_alive_scheduler_stalled`
- **Expected Behavior**: Distinguish process liveness (`last_process_heartbeat_at_ms`) from evaluation cycle health (`last_evaluation_completed_at_ms`, `last_successful_evaluation_at_ms`).
- **Actual Behavior Before Patch**: Watchdog heartbeat refreshed `last_process_heartbeat_at_ms` and masked stalled scheduler cycles.
- **Implementation Change**: Explicitly report separate `process_liveness` and `evaluation_health` indicators.
- **Integration Test**: `test_em07_process_heartbeat_valid_evaluation_stalled`
- **Regression Test**: `test_v17_6_1_telemetry_resilience.py`
- **Final Status**: Fixed & Verified

---

### EVID-09: Cycle and heartbeat persistence not fully reconciled
- **Root Cause**: `record_evaluation_cycle` placed cycles in the in-memory diagnostic buffer before DB commit, and caught DB errors while still updating engine heartbeat as if cycle succeeded.
- **Production Function**: `EngineObservabilityService.record_evaluation_cycle`
- **Reproduction Test**: `backend/tests/test_v17_6_4_1_cycle_commit_consistency.py::test_cc02_cycle_write_failure_degrades_telemetry`
- **Expected Behavior**: Telemetry records must not report success if DB write failed. In-memory diagnostic buffer only marks cycles durable after confirmed commit.
- **Actual Behavior Before Patch**: In-memory cycle returned as success even when DB write failed.
- **Implementation Change**: Reconcile in-memory state with persistence result; mark unpersisted cycles as `PERSISTENCE_FAILED`.
- **Integration Test**: `test_cc01_atomic_cycle_heartbeat_commit`
- **Regression Test**: `test_v17_6_2_cycle_durability.py`
- **Final Status**: Fixed & Verified

---

### EVID-10: Current tests insufficiently represent runtime SQLite
- **Root Cause**: Many unit tests used `sqlite:///:memory:` with `StaticPool`, which shares memory across threads and bypasses SQLite locking, WAL, and restart verification.
- **Production Function**: Test suites
- **Reproduction Test**: `backend/tests/test_v17_6_4_1_real_db_integration.py`
- **Expected Behavior**: Test with disk-backed SQLite files, multiple independent connections, simulated locked states, and restart readback.
- **Actual Behavior Before Patch**: In-memory database masked concurrency and restart bugs.
- **Implementation Change**: Add disk-backed SQLite integration suite (`test_v17_6_4_1_real_db_integration.py`).
- **Integration Test**: `test_real_db_concurrency_and_restart`
- **Regression Test**: Full backend suite
- **Final Status**: Fixed & Verified

---

### EVID-11: Replay/test origin not propagated through every caller
- **Root Cause**: Callers like `cg01_scheduler_service.py` passed `evaluation_origin="SCHEDULER"` instead of an authoritative origin, and test harnesses passed arbitrary strings that defaulted to `LIVE_FORWARD`.
- **Production Function**: `cg01_scheduler_service.py`, `scalp_watcher_orchestrator.py`
- **Reproduction Test**: `backend/tests/test_v17_6_4_1_origin_isolation.py::test_or04_test_origin_isolated`
- **Expected Behavior**: Authoritative `ObservationEvidenceOrigin` strictly validated at every boundary. Non-live origins (`REPLAY`, `TEST`, `UNKNOWN`) cannot generate live coverage.
- **Actual Behavior Before Patch**: Unknown origins defaulted to `LIVE_FORWARD`.
- **Implementation Change**: Implement `resolve_observation_evidence_origin()` helper; reject non-live origins from live telemetry.
- **Integration Test**: `test_or05_unknown_origin_rejected`
- **Regression Test**: `test_v17_6_4_cycle_run_identity.py`
- **Final Status**: Fixed & Verified

---

### EVID-12: Calendar schedule confused with operational health
- **Root Cause**: `get_contract_trading_availability` returned `"operational_status": "OPEN"` purely based on the static 24/7 Bitget contract specification without live operational verification.
- **Production Function**: `market_calendar_service.py::get_contract_trading_availability`
- **Reproduction Test**: `backend/tests/test_v17_6_4_1_engine_market_truth.py::test_em10_exchange_operational_state_unknown`
- **Expected Behavior**: Static calendar returns `scheduled_open=True`, `operational_status="UNKNOWN"` until live feed/transport evidence confirms operational health.
- **Actual Behavior Before Patch**: Hardcoded `"operational_status": "OPEN"`.
- **Implementation Change**: Return `scheduled_open=True` and `operational_status="UNKNOWN"` in `get_contract_trading_availability()`.
- **Integration Test**: `test_em11_valid_feed_reconciles_operational_state`
- **Regression Test**: `test_v17_6_4_perpetual_market_calendar.py`
- **Final Status**: Fixed & Verified

---

### EVID-13: Observation acceptance can hide missing gates
- **Root Cause**: In older acceptance runners, gates could be omitted from execution while the summary claimed all executed gates passed.
- **Production Function**: `tools/run_v17_6_4_1_acceptance.py`
- **Reproduction Test**: `backend/tests/test_v17_6_4_1_acceptance_runner.py::test_ac02_missing_required_gate_fails_verdict`
- **Expected Behavior**: Mandatory list of 30 gates (G00–G29). If any gate is missing, verdict is strictly `REJECTED`.
- **Actual Behavior Before Patch**: Verdict evaluated only executed gates.
- **Implementation Change**: Explicit list `MANDATORY_GATES = ["G00", ..., "G29"]`; enforce every gate is present and status is PASS.
- **Integration Test**: `test_ac01_all_mandatory_gates_pass`
- **Regression Test**: Acceptance runner self-test
- **Final Status**: Fixed & Verified

---

### EVID-14: Latest dashboard evidence may be stale or incomplete
- **Root Cause**: Observation dashboard did not clearly expose telemetry persistence failure alerts, unverified coverage flags, or distinguish process uptime from feed data coverage.
- **Production Function**: `ObservationDashboard.tsx`, `useObservationDashboard.ts`, `backend/main.py`
- **Reproduction Test**: `frontend/src/__tests__/v17_6_4_1_observation_evidence.test.tsx::test_ui04_coverage_persistence_failed_warning`
- **Expected Behavior**: Dashboard prominently displays persistence warnings, feed coverage quality, and unverified data badges without optimistic assumptions.
- **Actual Behavior Before Patch**: Displayed standard coverage percentage even when persistence degraded.
- **Implementation Change**: Expose persistence health in API; render distinct telemetry status in `ObservationDashboard.tsx`.
- **Integration Test**: Vitest suite
- **Regression Test**: Frontend build and lint
- **Final Status**: Fixed & Verified
