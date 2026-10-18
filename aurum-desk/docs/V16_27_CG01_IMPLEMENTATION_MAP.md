# V16.27 CG-01 Implementation Map

| Symbol / Class / Function | Module | Input / Output | Ownership / Transaction Boundary | Associated Tests |
|---|---|---|---|---|
| `_dispatch_lock` | `services/event_bus.py` | `threading.Lock` | Protects dispatch state transitions across threads and main loop | `test_001`, `test_002`, `test_014` |
| `_generation` | `services/event_bus.py` | `int` counter | Incremented on each `set_main_loop()` / `reset_for_test()` to invalidate obsolete callbacks | `test_008`, `test_015` |
| `_is_accepting` | `services/event_bus.py` | `bool` flag | Controls whether new dispatches are accepted (`False` after `begin_dispatch_shutdown()`) | `test_003`, `test_014` |
| `_pending_tokens` | `services/event_bus.py` | `Set[str]` | Tracks scheduled callbacks waiting to run on event loop | `test_007`, `test_012` |
| `_active_tasks` | `services/event_bus.py` | `Set[asyncio.Task]` | Holds strong references to active broadcast tasks on the loop | `test_010`, `test_011`, `test_013` |
| `_resolve_dispatch_loop` | `services/event_bus.py` | `() -> Tuple[loop, reason]` | Resolves target loop without instantiating any coroutine | `test_004`, `test_005`, `test_006` |
| `dispatch_websocket_broadcast` | `services/event_bus.py` | `(event_dict) -> None` | Deepcopies payload, registers token, schedules synchronous callback via `call_soon_threadsafe` | `test_001`, `test_002`, `test_009` |
| `_start_broadcast_on_loop` | `services/event_bus.py` | `(token, payload, gen, loop) -> None` | Callback on loop: checks gen, instantiates coroutine, creates `asyncio.Task`, attaches done callback | `test_001`, `test_008` |
| `_on_broadcast_done` | `services/event_bus.py` | `(task, event_id) -> None` | Done callback: observes cancellation and retrieves exception to prevent unretrieved warnings | `test_010`, `test_011` |
| `drain_websocket_broadcasts` | `services/event_bus.py` | `(timeout_s) -> Dict` | Waits until both `_pending_tokens` and `_active_tasks` are 0 using monotonic clock | `test_012`, `test_013`, `test_014` |
| `begin_dispatch_shutdown` | `services/event_bus.py` | `() -> None` | Sets `_is_accepting = False`, preserving accepted work for draining | `test_014` |
| `unbind_main_loop` | `services/event_bus.py` | `(expected_loop, expected_gen) -> None` | Safely clears main loop reference without clearing newer lifecycle | `test_015` |
| `on_after_rollback` | `services/event_bus.py` | Hook in `publish_event` | Cleans up `on_after_commit` listener if transaction rolls back, preventing leak on session reuse | `test_019`, `test_020` |
| `lifespan` shutdown | `main.py` | Lifecycle hook | Invokes `begin_dispatch_shutdown`, `drain_websocket_broadcasts`, and `unbind_main_loop` | `test_v16_15_scheduler_lifespan` |
