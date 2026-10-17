# V16.22 CG-01 Paper Trading Operations Guide

## 1. Distinction: Observation vs. Auto Execution
- **SHADOW Mode**: Evaluates incoming market structures and records telemetry traces without placing paper orders.
- **ACTIVE Mode**: Evaluates setups, verifies risk/daily guards, and arms paper limit/market orders when `auto_enabled=True`.

## 2. Lifespan Shutdown Sequence
- Shutdown follows strict ownership:
  1. Feeds & external collectors stop ingestion.
  2. Evaluation scheduler stops accepting new events (`_stopped = True`).
  3. Active scheduler in-flight tasks are drained via `await wait_evaluations_drained()`.
  4. Projection worker is stopped.
  5. Telemetry queue and retry buffer are drained asynchronously via `await drain_trace_persistence()`.
  6. Trace persistence worker is stopped.

## 3. Commit-Accurate Counters & Atomic In-Flight Ownership
- `_TRACE_PERSISTED_COUNT` increments only upon successful database commit, never on session flush or rollback.
- Items popped from `_RETRY_BUFFER` atomically increment `_IN_FLIGHT_COUNT` under lock, preventing false drain completions.

## 4. Production Config Parity
- Both ACTIVE and SHADOW callers resolve identical daily policy and config fingerprints from the DB `TradingPolicy`.
- Explicit user overrides in backtest or replay maintain highest precedence without being clobbered.
