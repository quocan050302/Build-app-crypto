# AURUM DESK V17.8.0.3 — MARKET DATA DISPATCH SAFETY & CANDLE INTEGRITY

**Release:** `V17.8.0.3`  
**Modules:** `backend/services/bitget_ws_service.py`, `backend/services/market_shadow_service.py`

---

## 1. Safe Market Shadow Dispatching

In `BitgetWSService`:
- `_resolve_active_shadow_sink()` checks if a shadow sink is registered or if `is_market_shadow_runtime()` is active.
- When active in `MARKET_SHADOW`:
  - `_dispatch_quote(quote)` routes validated quotes exclusively to `shadow_sink.ingest_quote(quote)` and optional UI read-only broadcaster callbacks, returning **before** `execution_consumer.enqueue_quote(quote)` or `candle_cache_service.process_quote(quote)` can ever be called.
  - `_dispatch_candle_payload(timeframe, data_rows, action, epoch)` routes candle payloads exclusively to `shadow_sink.ingest_candle_payload(...)`, returning **before** `candle_cache_service.process_candle_payload(...)` or `cg01_evaluation_scheduler` can ever be called.
- `process_ws_message(msg, epoch=..., connection_epoch=...)` accepts both raw JSON strings and dicts and returns structured routing metadata (`{"ok": ..., "route": "MARKET_SHADOW" | "OBSERVE_ONLY" | "TRADING", ...}`).

---

## 2. Canonical Quote Validation & Ordering Rules

`MarketShadowService.ingest_raw_quote` and `ingest_quote` enforce:
1. **Symbol & Contract Guard**: Rejects any symbol other than `XAUUSDT` (`WRONG_SYMBOL`) or non-`USDT-FUTURES` contract (`WRONG_CONTRACT_TYPE`).
2. **No Silent Last-Price Substitution**: Explicitly requires both executable `bid` and `ask` (`MISSING_EXECUTABLE_BID_OR_ASK`). Never substitutes `last` for `bid` or `ask`.
3. **Finite Geometry**: Rejects boolean, `NaN`, `Inf`, `<= 0`, or inverted (`bid > ask`) quotes.
4. **Timestamp & Epoch Integrity**: Rejects missing exchange timestamps, future-skewed timestamps, stale quotes (`> max_age_sec`), stale connection epochs (`< active_connection_epoch`), duplicate quotes (`DUPLICATE_QUOTE`), and out-of-order late quotes (`LATE_OUT_OF_ORDER_QUOTE`).

---

## 3. Public REST Fallback Without Candle Synthesis

- `MarketShadowService.poll_public_rest_ticker_fallback()` queries only the public Bitget ticker endpoint (`https://api.bitget.com/api/v2/mix/market/ticker`) with `source="REST_FALLBACK"`.
- Requires exchange timestamp `ts` from the payload (`REST_MISSING_EXCHANGE_TIMESTAMP` if absent).
- `MarketShadowService.synthesize_candle_from_ticker()` and `ingest_candle_payload(..., source="REST_TICKER_SYNTHETIC")` fail closed with `REST_FALLBACK_CANDLE_SYNTHESIS_FORBIDDEN`.

---

## 4. Multi-Timeframe Candle Finality & Gap Accounting

- `MarketShadowService.ingest_candle_payload` separates in-progress provisional bars (`is_closed=False`) from finalized closed bars (`is_closed=True`).
- An in-progress bar is finalized to `is_closed=True` when a newer interval timestamp arrives or when explicitly marked closed.
- Duplicate identical candle deliveries are ignored idempotently; legitimate revisions replace cumulative volume (`existing.volume = row["volume"]`, never `+=`) and increment `candles_revised`.
- Missing intervals (`delta > timeframe_ms`) increment `candles_missing` and set `has_data_gap = True` without ever fabricating synthetic bars.

