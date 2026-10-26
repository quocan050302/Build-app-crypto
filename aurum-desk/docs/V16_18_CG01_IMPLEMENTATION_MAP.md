# V16.18 CG-01 Implementation Map

| Component / Function | File | Transaction Ownership | Callers | Verification Node ID |
| :--- | :--- | :--- | :--- | :--- |
| `persist_trace` | `cg01_telemetry_service.py` | Dedicated worker commits; caller session only flushes | `record_trace`, `run_trace_persistence_worker` | `test_v16_18_red_reproductions.py::test_red_022_telemetry_commits_caller_uncommitted_transaction` |
| `evaluate_cg01_shadow_snapshot` | `cg01_scheduler_service.py` | Pure evaluation / zero DB side-effects | `evaluate_symbol_now` (shadow mode) | `test_v16_18_red_reproductions.py::test_red_021_shadow_isolation_violates_active_state` |
| `resolve_active_observation_run` | `paper_research_run_service.py` | Pure DB query with scope & mode filter | `evaluate_symbol_now`, `cg01_projection_worker` | `test_v16_18_red_reproductions.py::test_red_023_resolve_active_run_ignores_scope` |
| `_handle_armed_event` | `cg01_projection_worker.py` | Projection worker transaction | `process_pending_events` | `test_v16_18_red_reproductions.py::test_red_024_projection_attaches_old_event_to_new_run` |
| `record_pending_projection` | `cg01_projection_worker.py` | Worker transaction alongside checkpoint | `process_pending_events`, opened/closed handlers | `test_v16_18_red_reproductions.py::test_red_025_projection_checkpoint_skips_unlinked_events` |
| `build_cg01_paper_run_summary` | `paper_research_run_service.py` | Read-only query (DB authority) | Reports, dashboard API | `test_v16_18_red_reproductions.py::test_red_026_coverage_fails_after_restart_without_buffer` |
| `load_traces` | `cg01_telemetry_service.py` | Read-only query with true timestamp restoration | `get_traces`, `build_cg01_paper_run_summary` | `test_v16_18_red_reproductions.py::test_red_027_trace_timestamps_and_latencies_restoration` |
| `build_run_coverage_summary` | `paper_research_run_service.py` | Pure calculation with interval support | `build_cg01_paper_run_summary` | `test_v16_18_red_reproductions.py::test_red_028_uptime_vs_trace_health_with_gap` |
| `get_traces_by_mode` | `cg01_telemetry_service.py` | Memory + SQLite query by mode | Reports, diagnostic API | `test_v16_18_red_reproductions.py::test_red_029_trace_query_mixes_shadow_and_active` |