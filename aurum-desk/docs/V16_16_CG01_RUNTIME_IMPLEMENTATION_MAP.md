# V16.16 CG-01 Runtime Implementation Map

| Component / Function | File | Transaction Ownership | Callers | Verification Node ID |
| :--- | :--- | :--- | :--- | :--- |
| `get_or_create_run` | `paper_research_run_service.py` | Caller-owned (`db.flush()`) | `start_observation_run`, `build_cg01_paper_run_summary` | `test_v16_16_transaction_and_linkage.py::test_get_or_create_run_does_not_commit_caller_transaction` |
| `start_observation_run` | `paper_research_run_service.py` | Caller-owned (`db.flush()`) | Tests, API endpoints | `test_v16_16_transaction_and_linkage.py::test_start_and_complete_run_do_not_commit_caller_transaction` |
| `associate_order_with_research_run` | `paper_research_run_service.py` | Caller-owned (`db.flush()`) | `evaluate_symbol_now`, `_arm_paper_candidate` | `test_v16_16_transaction_and_linkage.py::test_association_supplements_missing_trace_metadata` |
| `record_committed_order_milestone` | `paper_research_run_service.py` | Caller-owned (`db.flush()`) | `_arm_paper_candidate`, `execute_fill`, `execute_close` | `test_v16_16_milestone_and_runtime_report.py::test_order_milestone_persists_and_is_readable_in_fresh_session` |
| `resolve_entry_execution_owner` | `cg01_scheduler_service.py` | Read-only | `evaluate_symbol_now`, `run_watcher_loop` | `test_v16_16_owner_and_shadow.py::test_shadow_owner_combination_keeps_watcher_as_execution_owner` |
| `run_watcher_loop` | `scalp_watcher_orchestrator.py` | Session-per-cycle | `main.py::lifespan` | `test_v16_16_owner_and_shadow.py::test_watcher_loop_uses_effective_owner_not_raw_is_owner` |
| `verify_entry_touch_coverage` | `cg01_missed_audit_service.py` | Pure calculation | `classify_opportunity` | `test_v16_16_audit_coverage_and_touch.py::test_coverage_verified_with_touch_is_touched_not_not_touched` |
| `classify_opportunity` | `cg01_missed_audit_service.py` | Pure calculation | `audit_run` | `test_v16_16_audit_coverage_and_touch.py::test_expired_without_coverage_is_unverifiable` |
| `audit_run` | `cg01_missed_audit_service.py` | Read-only query | `build_cg01_paper_run_summary` | `test_v16_16_audit_coverage_and_touch.py::test_audit_run_empty_associations_does_not_leak_auto_strategy` |
| `generate_runtime_paper_report_content` | `paper_research_run_service.py` | Read-only query | Acceptance runner, API | `test_v16_16_milestone_and_runtime_report.py::test_runtime_report_accurately_reflects_stopped_worker_and_empty_run` |