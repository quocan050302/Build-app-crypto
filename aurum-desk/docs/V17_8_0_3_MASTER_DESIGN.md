# AURUM DESK V17.8.0.3 — MASTER DESIGN SPECIFICATION

**Release:** `V17.8.0.3`  
**Branch:** `feat/v17-8-unicorn-structural-sl-tp-intelligence`  
**Trading Authorization:** `ZERO` (`PAPER_ORDERS_CREATED = 0`, `PAPER_ORDERS_FILLED = 0`, `PAPER_POSITIONS_CLOSED = 0`, `LIVE_EXCHANGE_ORDERS_SENT = 0`, `OPERATIONAL_DB_WRITES = 0`)

---

## 1. Executive Summary

Aurum Desk V17.8.0.3 introduces the **`MARKET_SHADOW`** runtime profile (`AURUM_BE_RUNTIME_PROFILE=MARKET_SHADOW`), continuous closed-bar SMC/ICT Unicorn shadow analysis, isolated shadow research storage, and real historical multi-timeframe backtesting across Variants A/B/C/D.

V17.8.0.2 introduced `OBSERVE_ONLY`, which suppressed all background workers (including the Bitget public market WebSocket) to guarantee operational database immutability. While safe for static API inspection, `OBSERVE_ONLY` left the market feed disconnected (`feed_connected = false`, `transport_state = DISCONNECTED`). Simply enabling `BitgetWSService` inside `OBSERVE_ONLY` would have been unsafe because `BitgetWSService._dispatch_quote()` and `CandleCacheService.process_candle_payload()` previously routed directly into `ExecutionConsumer`, `CandleCacheService` persistence, and `CG01EvaluationScheduler`.

V17.8.0.3 solves this architectural hazard by:

1. **Typed Capability Runtime Boundary (`backend/services/runtime_profile_service.py`)**:
   - Defines explicit `RuntimeCapabilities` for `TRADING`, `OBSERVE_ONLY`, and `MARKET_SHADOW`.
   - Enforces `MARKET_SHADOW_EXECUTION_FORBIDDEN` across every order-creation, fill, close, policy-mutation, and operational database write boundary.
2. **Safe Market Shadow Event Routing (`backend/services/bitget_ws_service.py` & `backend/services/market_shadow_service.py`)**:
   - Routes validated public Bitget WebSocket quotes and multi-timeframe candles (`1M`, `5M`, `15M`, `1H`, `4H`, `1D`) exclusively into `MarketShadowService` when running in `MARKET_SHADOW`.
   - Completely bypasses `execution_consumer`, `candle_cache_service` operational DB persistence, and `cg01_evaluation_scheduler`.
3. **Isolated Shadow Research Storage (`backend/services/shadow_research_storage.py`)**:
   - Persists shadow quotes, closed candles, analysis cycles, Unicorn confluence records, structural SL/TP decisions, shadow opportunities, and replay summaries into a dedicated SQLite database (`shadow_data/aurum_shadow_research.db` or `AURUM_SHADOW_DB_PATH`).
   - Enforces strict path-collision, symlink, hardlink, and table-ownership checks so the shadow database can never alias the operational trading database (`aurum_desk.db`).
4. **Continuous Closed-Bar SMC/ICT Unicorn Evaluation (`backend/services/market_shadow_service.py`)**:
   - Evaluates `SMC_M1_CONTINUATION`, `SMC_M1_REVERSAL`, `SMC_M1_SWEEP_RECLAIM`, `SMC_M1_FVG_CONTINUATION`, and `CG01_SWEEP_MSS_FVG` strictly on confirmed closed candles (`is_closed = True`, `close_time <= decision_cutoff_ms`) and validated executable Bid/Ask quotes.
   - Computes matched stop-loss and take-profit decisions across Legacy (Variant A), V17.7 Structural (Variant B), V17.8 Unicorn Structural + Dynamic Buffer (Variant C), and Noise-Multiplier Research (Variant D), plus authoritative `$2.50` risk sizing via `domain_calculator.calculate_trade_plan`.
5. **Real Historical Backtesting (`backend/services/v17_8_backtest_runner.py` & `backtest/*`)**:
   - Executes causal bar-by-bar replay over real historical `XAUUSDT` candles (`16,645` closed M1 bars, `32,602` closed M5 bars, `15,509` closed M15 bars; `0` fabricated bars) with explicit data quality verification, chronological 70/30 In-Sample / Out-of-Sample split, and spread/slippage/fee stress testing.

---

## 2. Target Architecture & Event Flow

```text
BITGET PUBLIC MARKET WEBSOCKET (wss://ws.bitget.com/v2/ws/public)
              │
              ▼
CANONICAL MARKET DATA VALIDATION (QuoteValidator / Bid+Ask Required)
              │
              ▼
QUOTE ORDERING / EPOCH VALIDATION (QuoteOrderingService)
              │
              ▼
SAFE MARKET SHADOW DISPATCHER (BitgetWSService -> MarketShadowService)
              │
       ┌──────┴──────┐
       ▼             ▼
  QUOTE STREAM  CANDLE STREAM (1M / 5M / 15M / 1H / 4H / 1D)
       │             │
       └──────┬──────┘
              ▼
  CLOSED-CANDLE FINALITY & CAUSAL CUTOFF GUARD
              │
              ▼
  SMC ENGINE + UNICORN EVIDENCE DETECTOR (evaluate_unicorn_confluence)
              │
              ▼
  STRUCTURAL SL/TP + AUTHORITATIVE RISK CALCULATOR ($2.50 Risk Budget)
              │
              ▼
  SHADOW OPPORTUNITY LIFECYCLE (NEVER_EXECUTED / RESEARCH ONLY)
              │
              ▼
  DEDICATED SHADOW RESEARCH DB  ──►  READ-ONLY /api/v1/shadow/* & UI
```

All order-execution boundaries remain unreachable in `MARKET_SHADOW`:

```text
SHADOW CANDIDATE  ─✕─►  PAPER ARM / PAPER FILL / PAPER CLOSE / LIVE ORDER
```

