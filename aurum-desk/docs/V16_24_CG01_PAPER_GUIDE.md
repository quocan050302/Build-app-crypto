# V16.24 CG-01 Paper Trading Operations Guide

## 1. Clock Domain Separation
- **Market Evidence Time (`market_evidence_as_of_ms`)**:
  Identifies the closed candle timestamp boundary for HTF structure, POI, sweeps, MSS, and FVG detection.
- **Execution Clock (`execution_now_ms`)**:
  Represents current wall-clock time. Controls quote freshness validation, news blackout enforcement, session window boundaries, candidate TTL expiry, and armed order timestamps.
- **Latency Attribution**:
  Queue delay is measured from event receipt to evaluation start; detector duration uses monotonic clock.

## 2. Telemetry Worker Batch Ownership & Cancellation Safety
- Every persistence operation is tracked via a `BatchHandle` in `_ACTIVE_BATCH_HANDLES`.
- When an async wrapper task is cancelled, the underlying synchronous database thread retains exclusive ownership of the batch.
- Running batches are never requeued or double-counted upon wrapper cancellation.
- Only unstarted items in a batch or retry group are safely preserved back to the retry buffer.

## 3. Worker Startup & Shutdown Lifespan Contracts
- **Startup**:
  `start_trace_persistence_worker()` blocks until the background worker has reached the `RUNNING` state and signaled its generation-specific ready event.
- **Shutdown**:
  Producers stop -> Evaluations drain -> Telemetry batches drain -> Worker stopped -> Lifespan records final structured status:
  `FAILED > TIMED_OUT > COMPLETED_WITH_LOSS > COMPLETED`.
