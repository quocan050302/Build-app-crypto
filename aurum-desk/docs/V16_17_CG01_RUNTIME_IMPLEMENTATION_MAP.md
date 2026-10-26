# V16.17 CG-01 Runtime Implementation Map

| Component / Function | File | Transaction Ownership | Callers | Verification Node ID |
| :--- | :--- | :--- | :--- | :--- |
| `resolve_active_observation_run` | `paper_research_run_service.py` | Read-only / Pure DB query | `get_or_create_run`, `evaluate_symbol_now`, `record_opportunity_transition` | `test_v16_17_red_reproductions.py::test_red_028_audit_misses_opportunity_without_order` |
| `record_opportunity_transition` | `opportunity_funnel_service.py` | Caller-owned (`db.flush()`) | `evaluate_symbol_now`, `evaluate_cycle` | `test_v16_17_red_reproductions.py::test_red_028_audit_misses_opportunity_without_order` |
| `CG01TraceRecorder.get_recent_traces` | `cg01_telemetry_service.py` | Pure memory + SQLite fallback | `generate_runtime_paper_report_content`, API endpoints | `test_v16_17_red_reproductions.py::test_red_026_coverage_fails_due_to_missing_get_recent_traces` |
| `CG01TracePersistenceWriter` | `cg01_telemetry_service.py` | Background auto-commit thread | `CG01TraceRecorder.record` | `test_v16_17_red_reproductions.py::test_red_027_traces_lost_after_memory_buffer_cleared` |
| `record_committed_order_milestone` | `paper_research_run_service.py` | Caller-owned (`db.flush()`) | `_arm_paper_candidate`, `execute_fill`, `execute_close` | `test_v16_17_red_reproductions.py::test_red_021_armed_milestone_not_recorded_during_arm` |
| `evaluate_symbol_now` | `cg01_scheduler_service.py` | Pure evaluation or delegates to orchestrator | Lifespan loop, manual trigger | `test_v16_17_red_reproductions.py::test_red_022_scheduler_uncommitted_post_cycle_research` |
| `_arm_paper_candidate` | `scalp_watcher_orchestrator.py` | Caller-owned atomic commit | `evaluate_cycle` | `test_v16_17_production_flow.py::test_scenario_1_long_tp_end_to_end` |
| `execute_fill` | `trade_lifecycle_service.py` | Atomic CAS commit | Watcher tick, price hit | `test_v16_17_production_flow.py::test_scenario_1_long_tp_end_to_end` |
| `execute_close` | `trade_lifecycle_service.py` | Atomic CAS commit (with nested savepoint) | Watcher tick, SL/TP trigger | `test_v16_17_production_flow.py::test_scenario_4_research_persistence_error_does_not_abort_close` |
| `audit_run` | `cg01_missed_audit_service.py` | Read-only query | `build_cg01_paper_run_summary` | `test_v16_17_production_flow.py::test_scenario_5_missed_opportunity_audit_detects_unarmed_blocked` |
| `CG01ResearchProjectionWorker` | `cg01_projection_worker.py` | Independent session auto-commit | `main.py::lifespan` | `test_v16_17_red_reproductions.py` |