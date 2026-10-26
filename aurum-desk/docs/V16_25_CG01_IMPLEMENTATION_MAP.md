# V16.25 CG-01 Implementation Map

| Symbol / Class / Function | Module | Input / Output | Ownership / Transaction Boundary | Associated Tests |
|---|---|---|---|---|
| `QueueReceipt` | `cg01_telemetry_service.py` | Enqueue item tracking | Atomic exactly-once queue task acknowledgement | `test_red_025`, `test_red_026`, `test_red_028`, `test_red_030` |
| `finalize_queue_receipt_once` | `cg01_telemetry_service.py` | `(receipt, terminal_status) -> bool` | Atomic CAS status check; calls `task_done()` exactly once | `test_red_025`, `test_red_027`, `test_red_028` |
| `RetryItem` | `cg01_telemetry_service.py` | Subclass of 2-tuple `(trace, attempt)` with `.receipt` attribute | Preserves `QueueReceipt` across retry attempts without breaking legacy 2-tuple unpacking | `test_red_025`, `test_red_029`, `test_v16_24_red_reproductions` |
| `BatchHandle` | `cg01_telemetry_service.py` | Dataclass tracking batch lifecycle | Owns `operation_task`, `receipts`, `thread_done_event` | `test_red_021_024`, `test_red_032`, `test_v16_25_flow_scenario_7` |
| `_ACTIVE_OPERATIONS` | `cg01_telemetry_service.py` | Dict registry of active asyncio Tasks | Tracks in-flight `asyncio.to_thread` tasks across waiter cancellations | `test_red_021_024`, `test_red_033`, `test_v16_25_flow_scenario_7` |
| `run_trace_persistence_worker` | `cg01_telemetry_service.py` | Telemetry loop coroutine | Shielded operation task dispatch; handles waiter cancellation gracefully | `test_red_021_024`, `test_red_032`, `test_red_033` |
| `drain_trace_persistence` | `cg01_telemetry_service.py` | Async flush and drain helper | Awaits queue empty, active operations count == 0, active batches == 0 | `test_red_026`, `test_red_033`, `test_v16_25_flow_scenario_1` |
| `wait_trace_persistence_worker_stopped` | `cg01_telemetry_service.py` | Worker shutdown verification | Checks active operations == 0 and batch handles == 0 | `test_v16_25_flow_scenario_7` |
