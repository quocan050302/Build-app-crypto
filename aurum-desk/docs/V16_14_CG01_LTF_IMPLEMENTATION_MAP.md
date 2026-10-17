# AURUM-DESK V16.14 IMPLEMENTATION MAP

| Area | Component / Service | File | Methods / Functions | Status |
| :--- | :--- | :--- | :--- | :---: |
| **Correctness** | HTF State Machine | `backend/services/htf_structure_service.py` | `step_htf_state`: Internal pullback fallthrough without reference blocker | **IMPLEMENTED** |
| **Correctness** | CG-01 Engine Bundle Filter | `backend/services/cg01_engine.py` | `build_cg01_context`: Strict closed filter (`is_closed is not False and close_ms <= now_ms`) | **IMPLEMENTED** |
| **Correctness** | HTF Collector Sync | `backend/services/collector_service.py` | `set_effective_config`, `update_htf_context`: Synchronize structural settings and as_of_ms | **IMPLEMENTED** |
| **Telemetry** | Trace & Ring Buffer | `backend/services/cg01_telemetry_service.py` | `CG01EvaluationTrace`, `CG01Span`, `CG01TraceRecorder`, `get_latency_summary` | **IMPLEMENTED** |
| **Scheduler** | Event-Driven LTF Scheduler | `backend/services/cg01_scheduler_service.py` | `capture_cg01_input_bundle`, `CG01EvaluationScheduler.notify_market_change`, `evaluate_symbol_now`, `run` | **IMPLEMENTED** |
| **Feed Wiring** | Authoritative Candle Cache | `backend/services/candle_cache_service.py` | `process_candle_payload`: Notify scheduler upon consistent batch mutation completion | **IMPLEMENTED** |
| **Orchestrator** | Watcher Loop Coexistence | `backend/services/scalp_watcher_orchestrator.py` | `run_watcher_loop`: Guarded watchdog mode avoiding double execution | **IMPLEMENTED** |
| **Funnel Audit** | Missed Opportunity Service | `backend/services/cg01_missed_audit_service.py` | `CG01MissedOpportunityAuditService.classify_opportunity`, `audit_run`: Distinguish POLICY_BLOCKED from technical misses | **IMPLEMENTED** |
| **Run & Accounting**| Forward Paper & Post-Fee Accounting | `backend/services/paper_research_run_service.py` | `PaperResearchRun`, `calculate_running_peak_drawdown`, `build_cg01_paper_run_summary`, Markdown & CSV exporters | **IMPLEMENTED** |
| **Funnel Stages** | M1 Scalp Domain Types | `backend/services/scalp_m1_types.py` | `OpportunityStage` (POI_WATCHED, SWEPT, MSS_CONFIRMED, FVG_CONFIRMED, ENTRY_READY), `OpportunityReasonCode` | **IMPLEMENTED** |
