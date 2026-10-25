# AURUM DESK V17.6.4.1 — REPLAY ISOLATION AUDIT
## Strict Provenance Arbitration & Telemetry Contamination Prevention

### 1. Defect Analysis (EVID-02 & EVID-11)
In previous versions, historical replay simulations or test fixtures executed within the application process could inadvertently write heartbeats or cycle records into the live forward observation tables. This risked polluting forward paper observation metrics with historical backtest or test harness uptime, creating fake observation coverage.

---

### 2. Observation Evidence Origin Model

V17.6.4.1 introduces the formal `ObservationEvidenceOrigin` enum:
- `LIVE_FORWARD`: Genuine forward observation with real-time exchange WebSocket/REST feeds.
- `REPLAY`: Historical replay or backtest evaluation.
- `TEST`: Unit test or integration test harness.
- `UNKNOWN`: Unspecified or ambiguous telemetry origin (fails closed as non-live).

#### Provenance Arbitration Rules (`resolve_observation_evidence_origin`):
1. If `engine_instance_id` contains `"replay"` or `"backtest"` -> `REPLAY`.
2. If `run_id` contains `"replay"` or `"backtest"` -> `REPLAY`.
3. If caller explicitly provides `evidence_origin="REPLAY"` -> `REPLAY`.
4. If `engine_instance_id` contains `"test"` or `"mock"` -> `TEST`.
5. Only if caller explicitly establishes live forward execution (`LIVE_FORWARD` or validated live engine) -> `LIVE_FORWARD`.

---

### 3. Hard Isolation in Telemetry Pipelines

#### In `ObservationIntervalService`:
- Active intervals key now incorporates `evidence_origin`:
  `active_key = (run_id, symbol, scope, worker_role, evidence_origin, engine_instance_id)`
- In `record_heartbeat_event(...)`:
  ```python
  if eff_origin != ObservationEvidenceOrigin.LIVE_FORWARD:
      # Non-live heartbeats are strictly isolated from forward paper intervals
      return None
  ```
  Non-live heartbeats are prohibited from creating or extending live forward observation coverage intervals!

#### In `EngineObservabilityService`:
- `record_evaluation_cycle` records `evidence_origin` on every cycle.
- Replay cycles are tagged with `evidence_origin="REPLAY"` and stored in isolated storage or flagged, preventing them from skewing live cycle counts, failure rates, or latencies.
- In `CG01SchedulerService`:
  Worker cycles explicitly set `evaluation_origin="LIVE_FORWARD"` when executing live forward paper monitoring.

---

### 4. Audit Verification
- Unit test suite `backend/tests/test_v17_6_4_1_origin_isolation.py` (cases OR01-OR12) confirms:
  1. Replay engines cannot extend live intervals.
  2. Test harness heartbeats are quarantined.
  3. No replay run can achieve `coverage_state="FULL"` in forward monitoring.
  4. 100% mathematical separation between backtest simulations and forward paper operations.
