# AURUM DESK V17.8.0.2 — BACKEND STARTUP & WORKER AUDIT (PHASE C1)

## 1. Audited Startup & Background Tasks in `backend/main.py`
A complete line-by-line audit of `backend/main.py` (`lifespan(app)` and module-level initialization) identified 4 startup mutators and 15 background `asyncio` tasks launched during normal `TRADING` startup:

### 1.1 Module-Level & Lifespan Startup Mutators
1. `models.Base.metadata.create_all(bind=engine)` and `database.run_schema_migrations(engine)` — Mutates SQLite schema/PRAGMAs on import if unguarded.
2. `reconcile_stuck_orders()` — Scans and mutates `PaperOrder` records in SQLite at startup.
3. `apply_runtime_contract_upgrades(db)` — Upgrades `SystemConfig` and `SessionPolicyContract` rows in SQLite at startup.
4. `position_recovery_service.check_and_recover_offline_positions(db)` — Evaluates and closes/updates offline `PaperOrder` positions at startup.
5. `start_trace_persistence_worker()` — Starts the CG01 telemetry persistence background worker.

### 1.2 Background Tasks in `lifespan(app)` (`ALL_BACKGROUND_TRADING_WORKERS`)
| # | Task / Worker | Normal `TRADING` Behavior | `OBSERVE_ONLY` Behavior |
|---:|---|---|---|
| 1 | `bitget_ws_service.run_ws_loop` | Connects outbound WS and feeds live quotes to execution consumer | **BLOCKED (Not started)** |
| 2 | `execution_consumer.run_consumer_loop` | Consumes quotes and triggers PAPER fills/exits | **BLOCKED (Not started + method guard)** |
| 3 | `candle_cache_service.initialize` / `run_persistence_worker` | Fetches and writes candles to SQLite | **BLOCKED (Not started)** |
| 4 | `collector_service.run_collector_loop` | Collects market data and persists to SQLite | **BLOCKED (Not started)** |
| 5 | `strategy_service.run_strategy_loop` | Evaluates 15M/5M setups and arms orders | **BLOCKED (Not started + method guard)** |
| 6 | `proximity_service.run_proximity_loop` | Updates setup proximity states in SQLite | **BLOCKED (Not started)** |
| 7 | `execution_coordinator.run_coordinator_loop` | Transitions ARMED orders to `paper_open` | **BLOCKED (Not started + method guard)** |
| 8 | `exit_monitor.run_exit_monitor_loop` | Monitors open positions and executes SL/TP closes | **BLOCKED (Not started + method guard)** |
| 9 | `market_reconciliation_service.run_reconciliation_loop` | Reconciles candle gaps in SQLite | **BLOCKED (Not started)** |
| 10 | `process_notification_outbox` | Dispatches and mutates notification outbox rows | **BLOCKED (Not started)** |
| 11 | `forward_repair_worker.run` | Repairs forward outcomes in SQLite | **BLOCKED (Not started)** |
| 12 | `lesson_repair_worker.run_loop` | Repairs post-trade lessons in SQLite | **BLOCKED (Not started)** |
| 13 | `scalp_watcher_orchestrator.run_watcher_loop` | Evaluates M1 scalp candidates and arms PAPER orders | **BLOCKED (Not started + method guard)** |
| 14 | `cg01_evaluation_scheduler.run` | Schedules CG01 evaluations and writes traces | **BLOCKED (Not started)** |
| 15 | `cg01_projection_worker.run` | Projects CG01 telemetry into SQLite | **BLOCKED (Not started)** |
