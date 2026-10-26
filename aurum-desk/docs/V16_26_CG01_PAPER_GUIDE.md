# V16.26 CG-01 Paper Trading Operational Guide

## Telemetry Loss Diagnostics
When inspecting drain results from `drain_trace_persistence()` or `flush_pending_traces()`, interpret status and loss counters as follows:

1. **Status Definitions**:
   - `COMPLETED`: All queued items and retries were durably committed to SQLite; `terminal_loss == 0`.
   - `COMPLETED_WITH_LOSS`: Drain completed cleanly (queue and retries empty, no in-flight DB operations), but one or more items failed permanently or were dropped due to queue overflow (`terminal_loss > 0`).
   - `IN_FLIGHT`: Operations or batch commits are currently running in DB worker threads.
   - `TIMED_OUT`: Timeout elapsed before all work could be cleanly processed or drained.

2. **Loss Metrics Breakdown**:
   ```python
   drain_result = await drain_trace_persistence(timeout_s=5.0)
   status = drain_result["status"]
   loss = drain_result["terminal_loss"]           # queue_drops + terminal_failures
   term_fails = drain_result["terminal_failures"] # traces dropped after retry exhaustion
   drops = drain_result["queue_drops"]            # traces dropped because queue was full
   attempts_failed = drain_result["failed"]       # attempt-level DB errors (transient + terminal)
   ```

3. **Shutdown Verification**:
   ```python
   from services.cg01_telemetry_service import stop_trace_persistence_worker, wait_trace_persistence_worker_stopped
   stop_trace_persistence_worker()
   stopped = await wait_trace_persistence_worker_stopped(timeout_s=3.0)
   assert stopped is True
   ```
