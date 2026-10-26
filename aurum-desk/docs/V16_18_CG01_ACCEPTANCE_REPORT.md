# V16.18 CG-01 Runtime Acceptance Report

- **Timestamp**: `2026-10-17T10:34:11.409034+00:00`
- **Commit SHA**: `7041fa8cd578b2462ed8c58282d44ccdccf77b95`
- **Diff Hash**: `344df2bd078956d65c9dea9718652ff1c87a5e741d2b4b62e16baa8717ca2b5f`
- **Overall Status**: **PASS**
- **Forward Status**: `COLLECTING` (Truthful runtime observation; 0 synthetic forward samples)

## Summary of Mandatory Technical Gates

| Gate | Focus Area | Status | Tests Passed | Duration |
| :--- | :--- | :--- | :--- | :--- |
| Gate 1: Isolation & Red Defect Verifications | PASS | 17 | 2.27s |
| Gate 2: Lifespan Integration & Owner Arbitration | PASS | 16 | 1.93s |
| Gate 3: Transaction Safety, Linkage & Milestones | PASS | 17 | 1.41s |
| Gate 4: Causal Missed Audit & Quote Coverage | PASS | 16 | 1.01s |
| Gate 5: HTF Structure & Production Flow | PASS | 31 | 1.71s |
| Gate 6: Full V16 Suite Non-Regression | PASS | 310 | 9.52s |
| Gate 7: Frontend Vitest Suite | PASS | 232 | 2.03s |

## Architectural Defects Repaired in V16.18
1. **Defect 1 - Telemetry Commits Caller Session (Rule 022, 031, 032)**: `CG01TracePersistenceWriter.persist_trace` no longer prematurely executes `target_db.commit()` on the caller session. Dedicated asynchronous worker sessions handle commits, while caller sessions are strictly flushed.
2. **Defect 2 - Shadow Isolation Violates Active State (Rules 021, 041-045)**: Scheduler shadow evaluation decouples completely from singleton `scalp_watcher_orchestrator.evaluate_cycle()`. Added pure adapter `evaluate_cg01_shadow_snapshot()` reusing the canonical CG-01 evaluator without mutating active watchlist, hypotheses, rejections, or business tables.
3. **Defect 3 - Run Resolver Ignores Scope (Rules 023, 051-053)**: `resolve_active_observation_run` now enforces strict scope match in SQL query and does not select runs based solely on symbol.
4. **Defect 4 - Cross-Run Event Contamination in Projection (Rules 024, 075)**: `CG01ResearchProjectionWorker._handle_armed_event` removes blind fallback to currently active run for older orders, ensuring historical orders are not improperly linked to new runs.
5. **Defect 5 - Checkpoint Skips Unlinked Domain Events (Rules 025, 074-080)**: Introduced explicit `ProjectionOutcome` (`PROJECTED`, `ALREADY_PROJECTED`, `RETRY_PENDING`, `UNVERIFIABLE`, `UNSUPPORTED`) and durable table `paper_research_pending_projections`. Unlinked events are persisted and retried with backoff instead of being permanently dropped.
6. **Defect 6 - Coverage Failure After Restart (Rules 026, 094, 095)**: `build_cg01_paper_run_summary` queries `cg01_trace_recorder.get_traces(symbol, run_id=run.run_id)` with SQLite fallback and merged ring buffer, ensuring historical run traces remain queryable after memory clear.
7. **Defect 7 - Distortion of Restored Timestamps and Latencies (Rules 027, 091-093)**: `load_traces` restores truthful timestamps (`evaluation_started_at`, `evaluation_completed_at`, `exchange_event_time_ms`, etc.) directly from JSON payload rather than overriding them with `created_at_ms`.
8. **Defect 8 - Conflation of Trace Health with Uptime (Rules 028, 111-115)**: `build_run_coverage_summary` introduces `trace_health_pct` for valid trace ratios, and requires `paper_observation_intervals` to report wall-clock `uptime_pct`, preventing 100% uptime claims during multi-hour downtime.
9. **Defect 9 - Cross-Mode Trace Pollution (Rules 029, 094, 095)**: `CG01TraceRecorder` and SQLite tables support `observation_mode` (`SHADOW` vs `ACTIVE`), enabling precise filtered queries by mode and run.