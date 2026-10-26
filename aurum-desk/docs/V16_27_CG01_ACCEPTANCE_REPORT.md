# V16.27 CG-01 Event Dispatch Lifecycle Acceptance Report

- **Timestamp**: `2026-10-18T04:02:39.982327+00:00`
- **Commit SHA**: `fb67e6dd91c36bb1f91e25f58ec470cd4a89c70d`
- **Diff Hash**: `0735c4e7f9a7e8d06bb4e2d0dc1d055b14d30a66aac88feeea260bd09b6a83e2`
- **Overall Status**: **PASS**
- **Forward Status**: `NOT_OBSERVED` (Truthful runtime observation; runner is standalone without attached daemon)
- **Trading Performance**: `NOT_EVALUATED` (Technical verification passing; 0 forward trading conclusions)
- **Unique Tests Verified**: `476` distinct tests (strictly deduplicated across gates)
- **Full V16 Test Files**: `67` test files in regression scope

## Summary of Mandatory Technical Gates

| Gate | Description | Status | Tests Passed | Elapsed |
|---|---|---|---|---|
| Gate 1: V16.27 Event Dispatch Lifecycle & Defect Verifications | `gate_1_dispatch_lifecycle_and_defects` | **PASS** | 133 | 43.94s |
| Gate 2: Scheduler Runtime & Owner | `gate_2_runtime_and_owner` | **PASS** | 16 | 10.49s |
| Gate 3: Transactions & Milestones | `gate_3_transaction_and_milestones` | **PASS** | 17 | 1.96s |
| Gate 4: Telemetry Batch Handles & Ownership | `gate_4_telemetry_ownership` | **PASS** | 16 | 1.07s |
| Gate 5: Queue Receipts & Production Flow | `gate_5_queue_and_production_flow` | **PASS** | 75 | 20.31s |
| Gate 6: Full V16 Regression Suite | `gate_6_v16_regression` | **PASS** | 476 | 68.12s |
| Gate 7: Frontend Vitest Integration | `gate_7_frontend` | **PASS** | 232 | 1.96s |

## Scope Verification and Root Cause Resolution

### Problem Root Cause
- **Defect**: In baseline V16.26, `EventBus.dispatch_websocket_broadcast()` evaluated `market_broadcaster.broadcast_domain_event(event_dict)` directly as an argument to `asyncio.run_coroutine_threadsafe(coro, self._main_loop)`. When `self._main_loop` was closed or stopped (e.g. between tests, during shutdown, or in race conditions), `run_coroutine_threadsafe` raised `RuntimeError`. The previously instantiated coroutine object was never scheduled or awaited, leaking until Python GC emitted `RuntimeWarning: coroutine 'MarketBroadcaster.broadcast_domain_event' was never awaited`.

### Implementation Fix
- **No Pre-Scheduling Coroutines**: `dispatch_websocket_broadcast()` never calls `market_broadcaster.broadcast_domain_event()` directly. Instead, it takes an immutable deepcopy snapshot of the payload and schedules a synchronous callback `_start_broadcast_on_loop` via `loop.call_soon_threadsafe()`.
- **Synchronous Callback Execution on Target Loop**: Only when `_start_broadcast_on_loop` actually executes on the active target loop does it instantiate the async coroutine and wrap it in `loop.create_task()`. If scheduling fails, zero coroutines were ever created, eliminating coroutine leaks.
- **Generation & Lifecycle Ownership**: Tracked via `_generation`, `_is_accepting`, `_pending_tokens`, `_active_tasks`, and `_dispatch_lock: threading.Lock`. Callbacks from obsolete generations are cleanly discarded without creating coroutines.
- **Safe Done Callback**: Task completion callback safely observes `task.cancelled()` and retrieves `task.exception()`, logging with `event_id` and preventing `Task exception was never retrieved` warnings.
- **Durable Outbox & Transaction Hook Cleanliness**: Added `after_rollback` listener in `publish_event()` ensuring that if a caller rolls back a session, the uncommitted event's `after_commit` hook is purged and never fires if the session is reused.
- **Graceful Shutdown & Draining**: Added `event_bus.begin_dispatch_shutdown()`, `drain_websocket_broadcasts(timeout_s=3.0)`, and `event_bus.unbind_main_loop()` in `backend/main.py` lifespan.

## Acceptance Assertions (Rules 191 - 197)
- **A. Closed/Stopped Loop**: Produces 0 coroutine leaks, 0 warnings, and does not leak unawaited coroutines.
- **B. Worker Thread Dispatch**: Synchronous caller dispatch safely offloads to bound main loop without running on worker thread.
- **C. Generation & Restart**: Old callbacks cannot replay into new lifecycles; restart unbinds and rebinds cleanly.
- **D. Regressions**: V16.25 and V16.26 suites pass 100%, and production LONG TP / SHORT SL flows maintain all order state invariants.
- **E. Full Suite**: All 100% mandatory gates pass on final code.

## Limitations & Operational Boundaries
- **Paper Only**: No live capital traded; Bitget exchange execution endpoints bypassed.
- **Forward Performance**: No predictive edge claimed; all data sourced from verified deterministic historical fixtures.
- **RAM Volatility**: WebSocket broadcasts are best-effort real-time UI notifications; durable business state is guaranteed via SQLite `domain_events` and `notification_outbox`.
