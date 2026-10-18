# V16.26 CG-01 Implementation Map

| Symbol / Class / Function | Module | Input / Output | Ownership / Transaction Boundary | Associated Tests |
|---|---|---|---|---|
| `_TRACE_TERMINAL_FAILURES` | `cg01_telemetry_service.py` | Global int counter | Tracks queued items that permanently failed persistence after exhausting retries | `test_red_001`, `test_red_010`, `test_red_013` |
| `get_terminal_failure_count` | `cg01_telemetry_service.py` | `() -> int` | Thread-safe accessor under `_RETRY_BUFFER_LOCK` | `test_red_001`, `test_red_018` |
| `get_queue_drops` | `cg01_telemetry_service.py` | `() -> int` | Thread-safe accessor under `_RETRY_BUFFER_LOCK` | `test_red_011`, `test_red_012` |
| `_get_terminal_loss_total` | `cg01_telemetry_service.py` | `() -> int` | Returns `queue_drops + terminal_failures` under `_RETRY_BUFFER_LOCK` | `test_red_012` |
| `_get_loss_accounting_snapshot` | `cg01_telemetry_service.py` | `() -> Dict[str, int]` | Returns atomic snapshot `{queue_drops, terminal_failures, terminal_loss}` | `test_red_001`, `test_red_021`, `test_red_022` |
| `finalize_queue_receipt_once` | `cg01_telemetry_service.py` | `(receipt, status) -> bool` | Atomically finalizes receipt, increments `_TRACE_TERMINAL_FAILURES` on `TERMINAL_FAILURE`, pops active registry | `test_red_001`, `test_red_004`, `test_red_023` |
| `reset_telemetry_worker_state` | `cg01_telemetry_service.py` | `() -> None` | Resets all telemetry worker globals including `_TRACE_TERMINAL_FAILURES = 0` | `test_red_018` |
| `flush_pending_traces` | `cg01_telemetry_service.py` | `(timeout_s) -> Dict` | Single-consumer synchronous drain with retry looping, returns `COMPLETED_WITH_LOSS` on terminal loss | `test_red_021`, `test_v16_19_red_reproductions` |
| `drain_trace_persistence` | `cg01_telemetry_service.py` | `(timeout_s) -> Dict` | Async drain evaluating `terminal_loss` snapshot for truthful `COMPLETED_WITH_LOSS` status | `test_red_001`, `test_red_020`, `test_red_024` |
