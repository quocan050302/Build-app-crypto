# AURUM DESK V17.6.4.3 — PRODUCTION CALL GRAPH & DATABASE TRANSITIONS

```text
+----------------------------------------------------------------------------------------+
|                          BITGET MARKET QUOTE / TICK DATA                               |
|              (WebSocket push / REST Fallback via BitgetWsService)                      |
+----------------------------------------------------------------------------------------+
                                           |
                                           v
+----------------------------------------------------------------------------------------+
|                          AUTHORITATIVE QUOTE VALIDATION                                |
|        resolve_observation_evidence_quality() / build_authoritative_quality()          |
|  - Bid > 0, Ask >= Bid, finite numeric sanity                                          |
|  - Canonical timestamp authority (exchange_ts_ms, received_at_ms)                      |
|  - Freshness threshold <= 2.0s                                                         |
|  - Transport state == "CONNECTED" (Primary WS)                                         |
|  - Replay / Test origin exclusion (LIVE_FORWARD only)                                  |
|  - Strict fail-closed: NO GOOD from raw booleans or untrusted dict/dataclass           |
+----------------------------------------------------------------------------------------+
                                           |
                                           v
+----------------------------------------------------------------------------------------+
|                      WATCHER / SCHEDULER HEARTBEAT PRODUCERS                           |
|        - ScalpWatcherOrchestrator.evaluate_cycle()                                     |
|        - Cg01SchedulerService / Cg01ExecutionCoordinator                              |
|        Supplies: Database Session, run_id, symbol, scope, worker_role,                 |
|                  canonical quote snapshot & transport proofs                           |
+----------------------------------------------------------------------------------------+
                                           |
                                           v
+----------------------------------------------------------------------------------------+
|                     ENGINE OBSERVABILITY SERVICE (Single Gateway)                      |
|        EngineObservabilityService.record_engine_heartbeat(db, ...)                     |
|        - Resolves target DB identity: resolve_observation_database_identity(db)        |
|        - Revalidates quote proofs via authoritative boundary                           |
|        - Sets verified_quality ("GOOD" | "DEGRADED" | "DOWN" | "UNVERIFIED")            |
|        - Upserts EngineHeartbeatRecord with verified metadata                          |
+----------------------------------------------------------------------------------------+
                                           |
                                           v
+----------------------------------------------------------------------------------------+
|                    OBSERVATION INTERVAL SERVICE (State Machine)                        |
|        ObservationIntervalService.record_heartbeat_event(db, ...)                      |
|        - Extracts database_identity (canonical file path or stable engine hash)        |
|        - Key active interval by (database_identity, run_id, symbol, scope, ...)        |
|        - Transitions active segments: ACTIVE -> SEALED_PENDING                         |
|        - Tags sealed pending interval with immutable database_identity                 |
+----------------------------------------------------------------------------------------+
                                           |
                                           v
+----------------------------------------------------------------------------------------+
|                     DATABASE-SCOPED PENDING QUEUE FLUSH                                |
|        ObservationIntervalService.persist_pending_intervals(target_db)                 |
|        - INVARIANT: item.database_identity == target_db.database_identity              |
|        - Prevents cross-database pending flushes (Database A cannot write to B)        |
|        - Creates owned telemetry Session bound to target engine                        |
|        - Inserts into PaperObservationInterval using deterministic identity            |
|        - State transition: PERSISTING -> COMMITTED -> ACKNOWLEDGED                     |
+----------------------------------------------------------------------------------------+
                                           |
                                           v
+----------------------------------------------------------------------------------------+
|                           SQLITE PERSISTENCE LAYER                                     |
|        Table: paper_observation_intervals                                              |
|        - Columns: id, scope, run_id, symbol, worker_role, mode, start_ms, end_ms,      |
|                   data_quality, heartbeats_count, created_at_ms, engine_instance_id,   |
|                   evidence_origin, interval_identity                                   |
|        - Unique Constraint / Index: uq_paper_obs_interval_identity                     |
+----------------------------------------------------------------------------------------+
                                           |
                                           v
+----------------------------------------------------------------------------------------+
|                       EVIDENCE HEALTH & DASHBOARD SNAPSHOT                             |
|        EngineObservabilityService.build_observation_dashboard_snapshot(db, ...)        |
|        - Evaluates TWO independent concepts:                                           |
|          1. last_durable_commit_status: COMMITTED | PENDING_COMMIT | UNVERIFIED        |
|          2. current_observation_evidence_health: HEALTHY | STALLED | DEGRADED | DOWN   |
|        - Scopes pending metrics to current database identity                           |
|        - Detects process restart gaps as explicit UNVERIFIED segments                  |
+----------------------------------------------------------------------------------------+
                                           |
                                           v
+----------------------------------------------------------------------------------------+
|                      REST API & FRONTEND OBSERVATION DASHBOARD                         |
|        - GET /api/v1/engine/observation/dashboard                                      |
|        - ObservationDashboard.tsx renders durable persistence status vs current        |
|          operational telemetry health                                                  |
+----------------------------------------------------------------------------------------+
```
