# AURUM V17.6.4.3a Implementation Map

## Overview
This document maps each changed production function to its original defect, new contract, callers, database behavior, and regression test.

---

### Function 1: `ObservationIntervalService._persist_interval_record()`
- **Original Defect**: `AC43A-01` — Borrowed session flush returned `IntervalPersistenceResult(status="COMMITTED")`.
- **New Contract**:
  - Owned session (`close_session=True`): Executes `target_db.commit()`, returns `status="COMMITTED"`.
  - Borrowed session (`close_session=False`): Executes `target_db.flush()`, returns `status="PENDING_COMMIT"`.
  - Anti-False-Duplicate Defense: Before returning `ALREADY_EXISTS`, verify via an independent database query whether the row is durably committed. If the row is only visible within the borrowed session's uncommitted transaction, return `status="PENDING_COMMIT"`.
- **Callers**:
  - `ObservationIntervalService.persist_pending_intervals(db)`
- **Database Behavior**:
  - Preserves ACID transaction boundary: Does not commit outer business transaction.
  - No database row marked committed until genuine transaction commit.
- **Tests**:
  - `backend/tests/test_v17_6_4_3a_commit_truth.py` (`CT01`, `CT02`, `CT03`, `CT04`)
  - `backend/tests/test_v17_6_4_3_transaction_boundaries.py` (`TX01`, `TX02`)

---

### Function 2: `ObservationIntervalService.persist_pending_intervals()`
- **Original Defect**: `AC43A-02` — Pending intervals evicted prematurely upon borrowed session flush.
- **New Contract**:
  - Transitions interval to `PENDING_COMMIT` state when flushed on borrowed session.
  - Interval is retained in `self._pending_intervals` and NOT evicted until genuine outer commit (`COMMITTED` or `ALREADY_EXISTS` confirmed by probe).
- **Callers**:
  - `ObservationIntervalService.record_heartbeat_event(db)`
  - `ObservationIntervalService.flush_active_intervals(db)`
  - Direct callers / background flusher.
- **Database Behavior**:
  - Retains pending telemetry across retries, rollbacks, and outer transactions.
- **Tests**:
  - `backend/tests/test_v17_6_4_3a_commit_truth.py` (`CT05`, `CT08`, `CT14`)

---

### Function 3: `EngineObservabilityService.record_engine_heartbeat()`
- **Original Defects**: `AC43A-03` and `AC43A-04` — Typed `ObservationEvidenceQuality` dataclass and worker role metadata allowed spoofing `GOOD` coverage without canonical market quotes.
- **New Contract**:
  - Strictly fails closed to `UNVERIFIED` (or `DOWN`/`DEGRADED` for trusted negative signals).
  - Can only grant `GOOD` if valid canonical quote evidence (`feed_data` with valid `symbol`, `bid > 0`, `ask >= bid`, `transport_state="CONNECTED"`, trusted `quote_source in ("WS", "WEBSOCKET")`, freshness `<= 2.0s`) is authoritatively validated by `resolve_observation_evidence_quality()`.
  - Removes typed object positive bypass and worker role `quote_source="WS"` fabrication.
- **Callers**:
  - `ScalpWatcherOrchestrator.evaluate_cycle()`
  - `CG01SchedulerService`
  - Engine evaluation loops and telemetry fixtures.
- **Database Behavior**:
  - Guarantees `models.PaperObservationInterval.data_quality == "GOOD"` rows only reflect genuine verified market data.
- **Tests**:
  - `backend/tests/test_v17_6_4_3a_authoritative_good.py` (`AG01`–`AG18`)

---

### Function 4: `EngineObservabilityService.build_engine_health_snapshot()`
- **Original Defect**: `AC43A-07` — Called `observation_interval_service.get_pending_metrics(run_id=...)` without passing `db=db`, reading global pending metrics across all databases.
- **New Contract**:
  - Passes `db=db` to `observation_interval_service.get_pending_metrics(db=db, run_id=target_run_id)`.
  - Returns database-isolated `evidence_persistence_status`.
- **Callers**:
  - `ObservationDashboard` API endpoints (`/api/observation/dashboard`, `/api/observation/engine-health`)
- **Database Behavior**:
  - Scopes persistence health strictly to destination database identity.
- **Tests**:
  - `backend/tests/test_v17_6_4_3a_health_db_scoping.py` (`HS01`–`HS10`)

---

### Function 5: `ScalpWatcherOrchestrator.evaluate_cycle()`
- **Original Defect**: Did not pass canonical quote snapshot into initial `record_engine_heartbeat` metadata.
- **New Contract**:
  - Attaches canonical quote snapshot (`symbol`, `bid`, `ask`, `quote_source`, `transport_state`, `last_quote_at_ms`, `quote_age_sec`, `status`) to `metadata["canonical_quote"]`.
  - Authorizes authoritative `GOOD` coverage when quote is fresh and valid.
- **Callers**:
  - Watcher background thread
  - Scheduler evaluation cadence
- **Database Behavior**:
  - Records authoritative `GOOD` heartbeats and intervals during genuine live monitoring.
- **Tests**:
  - `backend/tests/test_v17_6_4_3a_authoritative_good.py` (`AG14`, `AG15`)

---

### Function 6: `tools/run_v17_6_4_3a_acceptance.py` (`run_backend_discovery`, `run_cmd`)
- **Original Defects**: `AC43A-05` and `AC43A-06` — Pytest discovery passed with nonzero exit codes; unexpected skips did not fail release acceptance.
- **New Contract**:
  - `run_backend_discovery`: Requires `code == 0` AND `collected_count > 0`.
  - `run_cmd`: Parses `<skipped>` testcases from JUnit XML; fails release acceptance if any unapproved unexpected skips occur in mandatory suites.
- **Callers**:
  - Acceptance runner CLI
- **Tests**:
  - `backend/tests/test_v17_6_4_3a_discovery_integrity.py` (`DC01`–`DC10`)
  - `backend/tests/test_v17_6_4_3a_acceptance_integrity.py` (`AI01`–`AI16`)
