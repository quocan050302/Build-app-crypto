# V16.22 CG-01 Implementation Map

| File / Component | Function / Class | Changes Made | Transaction Owner | Side Effects | Verified In |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `backend/services/cg01_telemetry_service.py` | `CG01TracePersistenceWriter.persist_trace` | Moved counter increment inside `if should_close:` so that caller sessions only flush and commit counter owner increments after commit. | Caller DB session | Flushes trace model | `test_red_027` |
| `backend/services/cg01_telemetry_service.py` | `drain_trace_persistence` | Asynchronous non-blocking drain using `asyncio.sleep` to await queue, retry buffer, and in-flight commits without freezing the event loop. | Async event loop | Awaits persistence drain | `test_red_025` |
| `backend/services/cg01_telemetry_service.py` | `run_trace_persistence_worker` | Atomically increments `_IN_FLIGHT_COUNT += len(retry_items)` inside `with _RETRY_BUFFER_LOCK:` when popping from `_RETRY_BUFFER`. | Worker thread | Transfers retry ownership | `test_red_026` |
| `backend/services/cg01_scheduler_service.py` | `CG01EvaluationScheduler` | Added `_active_eval_tasks`, tracked evaluations in `run()`, stopped accepting new events on shutdown, and implemented `wait_evaluations_drained`. | Scheduler lifecycle | Tracks active tasks | `test_red_030` |
| `backend/services/cg01_scheduler_service.py` | `capture_cg01_input_bundle` | Defensively extracts attributes from both `dict` and `schemas.CandleCreate` objects. | Pure memory | None | `test_red_022` |
| `backend/main.py` | `lifespan` | Updated shutdown order: stop feeds -> stop scheduler -> await `wait_evaluations_drained` -> stop projection -> await `drain_trace_persistence` -> stop worker. | Lifespan context | Graceful async shutdown | `test_red_025`, `test_red_030` |
| `backend/services/cg01_config_factory.py` | `resolve_canonical_cg01_config` | Added `is_explicit_override` parameter; integrates DB `TradingPolicy` whenever `is_explicit_override is not True`. | Read-only session | Loads policy | `test_red_029` |
| `backend/services/scalp_watcher_orchestrator.py` | `evaluate_cycle` | Distinguishes production-loaded config from explicit test overrides via `_is_explicit_override`, passing `is_explicit_override` to factory. | Caller DB session | Resolves policy | `test_red_029` |
| `backend/services/scalp_m1_types.py` | `load_effective_scalp_config` | Added parsing for `cg01_enabled`, `cg01_profile_mode`, and `execution_profile` from `SystemConfig`. | Read-only session | Loads overrides | `test_red_022`, `test_v16_22_scenario_1` |
