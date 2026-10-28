# AURUM DESK V17.8.0.2 — OBSERVATION SAFETY CONTRACT (PHASE C2)

## 1. Multi-Layer Enforcement Architecture (`AURUM_BE_RUNTIME_PROFILE=OBSERVE_ONLY`)

Aurum Desk V17.8.0.2 implements a 5-layer defense-in-depth contract in `backend/services/runtime_profile_service.py`, `backend/database.py`, `backend/main.py`, `backend/crud.py`, and the execution services:

1. **Layer 1 — Fail-Closed Runtime Profile Resolution (`runtime_profile_service.py`):**
   - `AURUM_BE_RUNTIME_PROFILE` accepts only `TRADING` (default) or `OBSERVE_ONLY` (case-insensitive).
   - Any unrecognized profile value immediately raises `RuntimeError("INVALID_BE_RUNTIME_PROFILE: ...")`, preventing ambiguous startup.
2. **Layer 2 — Lifespan Startup & Worker Suppression (`main.py`):**
   - When `is_observe_only_runtime()` is `True`, `main.lifespan` skips `reconcile_stuck_orders()`, `apply_runtime_contract_upgrades()`, `check_and_recover_offline_positions()`, `start_trace_persistence_worker()`, and all 15 background trading workers.
   - Records `status="HEALTHY_OBSERVE_ONLY"` and `background_trading_workers_started=0`.
3. **Layer 3 — Execution & Service Boundary Guards:**
   - Direct calls to `scalp_watcher_orchestrator._arm_paper_candidate`, `scalp_watcher_orchestrator.run_watcher_loop`, `execution_coordinator.evaluate_orders_sync`, `execution_coordinator.run_coordinator_loop`, `execution_consumer.evaluate_quote_sync`, `execution_consumer.run_consumer_loop`, `TradeLifecycleService.execute_fill`, `TradeLifecycleService.execute_close`, `TradeLifecycleService.process_exit_tick`, `exit_monitor.check_active_position`, `exit_monitor.run_exit_monitor_loop`, `strategy_service.run_strategy_loop`, `crud.create_paper_order`, and `crud.update_paper_order_state` are guarded by `assert_not_observe_only(...)` and increment dedicated blocked-attempt counters.
4. **Layer 4 — HTTP Middleware Mutation Block (`main.py`):**
   - `enforce_observe_only_http_boundary` intercepts all `POST`, `PUT`, `PATCH`, `DELETE` requests and mutating `GET` routes (`/api/v1/reports/generate`), returning HTTP `403` with `{"detail": {"code": "OBSERVE_ONLY_MUTATION_BLOCKED", ...}}` and incrementing `BLOCKED_HTTP_MUTATION_ATTEMPTS`.
5. **Layer 5 — SQLite Connection & SQLAlchemy ORM Write Guards (`database.py`):**
   - Rejects targeting operational `aurum_desk.db` when `OBSERVE_ONLY` is active.
   - Sets `PRAGMA query_only=ON` on every SQLite connection (and skips `PRAGMA journal_mode=WAL` so connection opening never mutates file bytes).
   - SQLAlchemy `before_cursor_execute` (`_enforce_observe_only_sql_guard`) and `before_flush` (`_enforce_observe_only_orm_flush_guard`) listeners reject `INSERT`, `UPDATE`, `DELETE`, `REPLACE`, `CREATE`, `ALTER`, `DROP`, `VACUUM`, `REINDEX`, `ATTACH`, `DETACH`, and mutating `PRAGMA` statements with `RuntimeError("OBSERVE_ONLY_DB_WRITE_BLOCKED: ...")`.
