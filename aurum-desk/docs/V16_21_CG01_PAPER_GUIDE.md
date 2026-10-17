# V16.21 CG-01 Paper Trading Operations Guide

## 1. Distinction: Observation vs. Auto Execution
- **SHADOW Mode**: Evaluates incoming market structures and records telemetry traces without placing paper orders.
- **ACTIVE Mode**: Evaluates setups, verifies risk/daily guards, and arms paper limit/market orders when `auto_enabled=True`.

## 2. Shared Canonical Configuration
- Both ACTIVE and SHADOW modes resolve strategy parameters using `resolve_canonical_cg01_config`.
- Configuration precedence: Explicit caller override -> Persisted database profile -> Canonical defaults.
- Fingerprint is computed deterministically using MD5 over the 24 decision-affecting fields.

## 3. Quote Contract
- All market quotes must be validated via `QuoteValidator.normalize_execution_quote`.
- Consumers access attributes directly: `quote.bid`, `quote.ask`, `quote.last`, `quote.spread`, `quote.exchange_time`, `quote.observed_at`.
- Stale quotes (>15s) or inverted spreads are marked invalid and skipped safely.

## 4. Telemetry Resilience
- Database session creation errors are caught safely; batches are retained in `_RETRY_BUFFER` (up to 3 attempts with exponential backoff).
- Single-consumer drain ensures `flush_pending_traces` waits for in-flight commits without concurrent competition.
