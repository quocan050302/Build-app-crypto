# V16.23 CG-01 Paper Trading Operations Guide

## 1. Automatic Event-Driven Scheduler
- The scheduler runs as an asyncio daemon listening for candle close events from `candle_cache_service`.
- When a 1M candle closes, the cache automatically notifies the scheduler via `notify_market_change(event)`.
- The scheduler computes causal timestamps, captures quotes, evaluates HTF context/POI/Sweep/MSS/FVG, and arms paper orders without manual intervention.

## 2. Telemetry Worker Lifecycle & Item Ownership
- **Startup**: `start_trace_persistence_worker()` awaits `_TRACE_WORKER_READY_EVENT` before the application begins accepting requests.
- **Worker States**: `STOPPED -> STARTING -> RUNNING -> STOPPING -> STOPPED` (or `FAILED`).
- **Item Ownership**: Accepted items are strictly owned in one state (`QUEUED`, `WORKER_OWNED/IN_FLIGHT`, `RETRYING`, or `COMMITTED`). Dequeued items are preserved across cancellations or session open failures.

## 3. Verified Shutdown & Thread Completion
- Shutdown sequence:
  1. Feeds & external collectors stop ingestion.
  2. Scheduler stops accepting new events (`_stopped = True`).
  3. `await wait_evaluations_drained()` waits for both asyncio task wrappers AND active worker threads in `_active_eval_threads`.
  4. Projection worker is stopped.
  5. `await drain_trace_persistence()` drains remaining queue and retry items.
  6. Worker task is stopped and awaited.
  7. Lifespan logs structured shutdown results (`COMPLETED`, `COMPLETED_WITH_LOSS`, or `TIMED_OUT`).
