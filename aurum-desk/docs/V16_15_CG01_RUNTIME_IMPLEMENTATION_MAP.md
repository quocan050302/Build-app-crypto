# V16.15 CG-01 Implementation Map & Architecture Blueprint

## 1. Modified & Created Files

### Backend Core Models & Database Migrations
- `backend/models.py`:
  - Added `PaperResearchRunRecord` (persistent SQLite table for observation runs).
  - Added `PaperResearchOrderLink` (exact run↔order↔opportunity association table with unique constraint).
- `backend/database.py`:
  - Added additive migration in `run_schema_migrations()` for `paper_research_run_records` and `paper_research_order_links`.

### Scheduler & Runtime Lifespan
- `backend/main.py`:
  - Added `cg01_evaluation_scheduler.run()` to `lifespan(app)` tasks registry.
  - Added `cg01_evaluation_scheduler.stop()` in lifespan clean shutdown.
  - Decorated `lifespan(app)` with `@asynccontextmanager`.
- `backend/services/cg01_scheduler_service.py`:
  - Fixed `input_versions` in `capture_cg01_input_bundle()` to use revision watermarks.
  - Fixed `evaluate_symbol_now()`: removed forced `auto_enabled=True`; respects `strategy_service.get_auto_state(db)`.
  - Added `get_health_snapshot()` returning `CONFIGURED`, `RUNNING`, `STOPPED`, and effective owner.
  - Added `resolve_entry_execution_owner()` arbitrator for single execution owner.
  - Added `resolve_effective_cg01_config()` with 16-character content fingerprint.

### Trustworthy Paper Accounting
- `backend/services/paper_research_run_service.py`:
  - Implemented persistent database storage in `get_or_create_run()` and `complete_run()`.
  - Implemented `associate_order_with_research_run()` and `reconcile_run_accounting()`.
  - Fixed `build_cg01_paper_run_summary()`: reads `realized_pnl_net`, recognizes `paper_open`, eliminates double-fee deduction, sorts closed trades chronologically.

### Causal Opportunity Audit
- `backend/services/cg01_missed_audit_service.py`:
  - Added quote tape coverage check in `classify_opportunity()` (missing coverage -> `UNVERIFIABLE`/`DATA_GAP`).
  - Separated scheduler delay violation from technical runaway misses.
  - Recognized `paper_open` as filled and active armed setups as `PENDING`.
  - Eliminated substring matching in `audit_run()`, enforcing exact equality.

### Automated Test Suites
- `backend/tests/test_v16_15_scheduler_lifespan.py`
- `backend/tests/test_v16_15_auto_permission_and_owner.py`
- `backend/tests/test_v16_15_paper_accounting_and_persistence.py`
- `backend/tests/test_v16_15_causal_audit_classification.py`
- `tools/run_v16_15_cg01_runtime_acceptance.py`