# AURUM DESK V17.8.0.3 — EXISTING ARCHITECTURE MAP & CALL-GRAPH AUDIT

**Release:** `V17.8.0.3`  
**Phase:** Phase A Architecture Audit

---

## 1. Call-Graph of Market Data Entry to Execution & Persistence

### 1.1 Public Market Data Entry & Quote Validation
1. **Entry Point**: `BitgetWSService.run_ws_loop()` (`backend/services/bitget_ws_service.py`) connects to `wss://ws.bitget.com/v2/ws/public` and subscribes to `ticker` and `candle1m`, `candle5m`, `candle15m`, `candle1H`, `candle4H`, `candle1D` for `XAUUSDT` (`USDT-FUTURES`).
2. **Canonical Quote Validation**: `BitgetWSService._parse_ticker_payload()` delegates to `QuoteValidator.validate_canonical()` (`backend/services/quote_validator.py`), which verifies finite positive `bid`, `ask`, `bid <= ask`, non-negative spread, and exchange timestamp freshness.
3. **Quote Ordering**: `QuoteOrderingService.classify_quote()` (`backend/services/quote_ordering_service.py`) classifies each quote into `ACCEPT_CURRENT`, `DUPLICATE`, `LATE_EVIDENCE`, or `REJECT_INVALID` based on `(symbol, connection_epoch, exchange_ts_ms, payload_digest)`.

### 1.2 Pre-V17.8.0.3 Hazard Path in `BitgetWSService` & `CandleCacheService`
Prior to V17.8.0.3:
- `BitgetWSService._dispatch_quote()` unconditionally invoked:
  1. `execution_consumer.enqueue_quote(quote)` -> triggers `ExecutionConsumer.evaluate_quote_sync()` -> `PaperBroker.check_pending_fills()` (mutates `PaperOrder` to `paper_open`) and `TradeLifecycleService.process_exit_tick()` (closes open paper positions).
  2. `candle_cache_service.process_quote(quote)`.
  3. `market_broadcaster.broadcast_quote(quote)`.
- `BitgetWSService._dispatch_candle_payload()` unconditionally invoked `candle_cache_service.process_candle_payload()`, which:
  1. Enqueued candle persistence to `candle_cache_service._persistence_queue` (writes to operational `candles` table in `aurum_desk.db`).
  2. Notified `cg01_evaluation_scheduler.notify_candle_closed()` -> triggers strategy evaluation and `ScalpWatcherOrchestrator._arm_paper_candidate()` -> `crud.create_paper_order()`.

---

## 2. Purity & Reusability Audit of Existing Quantitative Modules

| Module / Function | Side Effects? | Reusability in `MARKET_SHADOW` |
|---|---|---|
| `QuoteValidator.validate_canonical` | Pure | Reused directly with additional strict raw Bid/Ask presence guard |
| `QuoteOrderingService` | In-memory state per instance | Instantiated as a dedicated instance inside `MarketShadowService` |
| `SMCEngine` (`detect_swings`, `detect_fvgs`, `detect_structure`, `detect_equal_highs_lows`, `calculate_atr_series`) | Pure over bar sequences | Reused directly on closed `ShadowCandleRecord` sequences |
| `evaluate_unicorn_confluence` (`backend/services/unicorn_evidence_service.py`) | Pure | Reused directly on closed M1/M5/M15 candle series |
| `select_structural_invalidation_anchor`, `compute_stop_buffer`, `build_stop_placement_decision`, `select_structural_target` (`backend/services/stop_placement_service.py`) | Pure | Reused directly |
| `build_stop_comparison_plan` (`backend/services/stop_placement_research_service.py`) | Pure | Reused directly to produce matched Variants A/B/C/D per opportunity |
| `calculate_trade_plan` (`backend/domain_calculator.py`) | Pure | Reused directly as authoritative risk/quantity/fee/R:R calculator |
| `ScalpWatcherOrchestrator._arm_paper_candidate` | Writes operational DB (`PaperOrder`) | Strictly forbidden in `MARKET_SHADOW` (`MARKET_SHADOW_EXECUTION_FORBIDDEN`) |
| `ExecutionConsumer.evaluate_quote_sync` | Mutates operational DB (`PaperOrder`) | Strictly forbidden in `MARKET_SHADOW` (`MARKET_SHADOW_EXECUTION_FORBIDDEN`) |
| `TradeLifecycleService.execute_fill` / `execute_close` | Mutates operational DB (`PaperOrder`) | Strictly forbidden in `MARKET_SHADOW` (`MARKET_SHADOW_EXECUTION_FORBIDDEN`) |

