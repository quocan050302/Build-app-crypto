# V16.17 CG-01 Runtime Acceptance Report

- **Timestamp**: `2026-10-17T08:33:56.390807+00:00`
- **Commit SHA**: `b025a49e54b23eb2c23ed643efd07de37c02be96`
- **Diff Hash**: `c272a8f5680eecd113e91e9c22b01c2fd0482b9a23fa3bf47ef9589dd43cb8ef`
- **Overall Status**: **PASS**
- **Forward Status**: `COLLECTING` (Truthful runtime observation; 0 synthetic forward samples)

## Summary of Mandatory Technical Gates

| Gate | Focus Area | Status | Tests Passed | Duration |
| :--- | :--- | :--- | :--- | :--- |
| Gate 1: Lifespan Integration & Owner Arbitration | PASS | 19 | 2.88s |
| Gate 2: Transaction Safety, Linkage & Milestones | PASS | 22 | 1.27s |
| Gate 3: Causal Missed Audit & Quote Coverage | PASS | 16 | 0.9s |
| Gate 4: HTF Structure & Production Flow | PASS | 31 | 1.29s |
| Gate 5: Full V16 Suite Non-Regression | PASS | 301 | 8.66s |
| Gate 6: Frontend Vitest Suite | PASS | 232 | 1.6s |

## Architectural Defects Repaired in V16.17
1. **Defect A - Scheduler Uncommitted Post-Cycle Research Staging**: `evaluate_symbol_now` no longer leaves uncommitted research writes on a dirty session after `evaluate_cycle()`. `evaluate_cycle()` handles research linking and milestone recording atomically within its single transaction, or in shadow mode purely runs candidate evaluation.
2. **Defect B - Inverted Arm Milestone Link Query**: Fixed the reversed filter in `record_committed_order_milestone(milestone='ARMED')` where `models.PaperResearchOrderLink.run_id == order_id` was queried instead of `models.PaperResearchOrderLink.order_id == order_id`.
3. **Defect C - Uncommitted Close Research Failure Session Corruption**: Wrapped research milestone writes on trade close in nested savepoints (`db.begin_nested()`) and backed by asynchronous `CG01ResearchProjectionWorker` reading committed domain events.
4. **Defect D - Missing `get_recent_traces` Telemetry Method**: Added `get_recent_traces` method to `CG01TraceRecorder` as an authoritative alias for `get_traces`.
5. **Defect E - Transient Memory Ring Buffer Trace Loss**: Created durable `CG01TracePersistenceWriter` writing trace records to SQLite table `cg01_trace_records`, persisting trace latencies across process restarts and memory buffer clears.
6. **Defect F - Missed Opportunity Audit Without Paper Orders**: Updated `CG01MissedOpportunityAuditService.audit_run` to query `OpportunityFunnelRecord` by direct `run_id` match in addition to order linkage, guaranteeing audit visibility for unarmed/blocked opportunities.
7. **Defect G - Missing Direct `run_id` on Funnel Records**: Added `run_id` column to `OpportunityFunnelRecord` and `OpportunityEvaluation`, automatically resolving active observation run when omitted.
8. **Defect H - Shadow Mode Singleton Mutation**: Decoupled shadow evaluation in `evaluate_symbol_now` by invoking pure `evaluate_cg01_candidate` rather than mutating production `scalp_watcher_orchestrator` singleton state.