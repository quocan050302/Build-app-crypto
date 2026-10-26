# V16.27 CG-01 Paper Trading Operational Guide

## Event Dispatch Diagnostics
When monitoring real-time domain event WebSocket dispatch and shutdown, inspect the following diagnostics:

1. **Lifespan Shutdown Result**:
   ```python
   from main import get_last_shutdown_result
   res = get_last_shutdown_result()
   ws_drain = res.get("websocket_drain", {})
   # ws_drain format:
   # {
   #   "status": "COMPLETED" | "TIMED_OUT",
   #   "pending_count": int,
   #   "active_task_count": int,
   #   "elapsed_ms": int
   # }
   ```

2. **Draining Semantics**:
   - `COMPLETED`: All accepted domain event broadcasts were dispatched and sent to connected WebSocket clients before shutdown.
   - `TIMED_OUT`: Timeout elapsed before slow clients could be sent the final events; remaining tasks were cleanly cancelled and unawaited warnings prevented.

3. **Domain Event Re-synchronization**:
   - Real-time WebSocket delivery is non-durable and intended for UI reactivity.
   - Authoritative business state is permanently persisted in the `domain_events` table and may be queried at any time via `/api/v1/events?since_sequence=...`.
