# AURUM V17.6.4.3b — CANONICAL FEED PROVENANCE CONTRACT
**Version:** V17.6.4.3b  
**Release Scope:** Truthful Market Feed Provenance & Canonical Evidence Invariants  
**Target Symbol:** XAUUSDT (USDT-Futures)  
**Contract Author:** Principal Market Data Infrastructure Engineer & Systems Auditor  

---

## 1. Executive Summary & Core Invariant

The integrity of quantitative trading, position arming, and observation audit trails rests unconditionally on **unforgeable feed provenance**. Prior to V17.6.4.3b, edge-case fallbacks in watcher orchestration allowed missing exchange timestamps to default to local receive time (`q_ts = ... or t_recv`), missing quote sources to default to `"WS"` (`q_src = ... or "WS"`), and disconnected transport states to default to `"CONNECTED"` (`ws_trans if ws_trans in (...) else "CONNECTED"`). 

Under V17.6.4.3b, these vulnerabilities (`CFP-01`, `CFP-02`, `CFP-03`, `CFP-04`) are definitively eliminated. The canonical feed contract strictly enforces:
1. **Zero Timestamp Fabrication:** Missing exchange timestamps fail closed with `is_quote_valid=False`, `reason_code="QUOTE_TIMESTAMP_UNVERIFIED"`. Local receive time is recorded strictly in `received_at_ms` and is never substituted into `exchange_ts_ms`.
2. **Zero Source Fabrication:** Quote source must be authoritatively proven from the underlying feed adapter or raw exchange payload. If absent, it is classified as `"UNKNOWN"`, causing fail-closed degradation.
3. **Zero Transport State Fabrication:** Transport states (`CONNECTED`, `DEGRADED`, `RECONNECTING`, `DISCONNECTED`) are preserved exactly as reported by `BitgetWSService`. A disconnected transport is never mapped or defaulted to `CONNECTED`.
4. **Epoch Freshness:** Quotes generated in a prior connection epoch are rejected with `reason_code="CONNECTION_EPOCH_MISMATCH"` and cannot produce `GOOD` observation coverage.

---

## 2. Provenance Resolution Function Specification

```python
def resolve_watcher_observation_provenance(
    *,
    raw_quote: Optional[Any],
    validated_quote: Optional[ValidatedQuote],
    feed_adapter_state: Optional[Any],
    evaluated_at_ms: Optional[int] = None,
) -> WatcherObservationProvenance
```

### 2.1 Inputs & Preconditions
- `raw_quote`: Raw payload received from exchange WebSocket or REST ticker.
- `validated_quote`: Output of `normalize_execution_quote()`.
- `feed_adapter_state`: Live `BitgetWSService` singleton.
- `evaluated_at_ms`: Current monotonic execution clock in milliseconds.

### 2.2 Truth Table & State Mapping

| Raw Quote State | Adapter Transport | Adapter Epoch | Resolved Source | Resolved Transport | Status | Quality |
|---|---|---|---|---|---|---|
| Fresh WS quote with exchange ts | `CONNECTED` | Epoch 1 == Quote Epoch | `"WS"` | `"CONNECTED"` | `VALID` | `GOOD` |
| Quote missing exchange ts | `CONNECTED` | Epoch 1 | Preserved | `"CONNECTED"` | `UNVERIFIED` | `UNVERIFIED` |
| Quote missing source | `CONNECTED` | Epoch 1 | `"UNKNOWN"` | `"CONNECTED"` | `UNVERIFIED` | `UNVERIFIED` |
| Quote epoch 0, Adapter epoch 1 | `CONNECTED` | Epoch 1 != Quote Epoch | Preserved | `"CONNECTED"` | `DEGRADED` | `DEGRADED` |
| Adapter transport `DISCONNECTED` | `DISCONNECTED` | Any | Preserved | `"DISCONNECTED"` | Preserved | `DOWN` |
| Adapter transport `RECONNECTING` | `RECONNECTING` | Any | Preserved | `"RECONNECTING"` | Preserved | `DEGRADED` |
| Adapter transport `DEGRADED` | `DEGRADED` | Any | Preserved | `"DEGRADED"` | Preserved | `DEGRADED` |
| REST fallback ticker | `DISCONNECTED` | Any | `"REST_FALLBACK"` | `"DISCONNECTED"` | `VALID` | `DEGRADED` |

---

## 3. Behavioral Invariants & Strategy Protection

- **SMC/ICT Strategy Isolation:** No mathematical changes to Fair Value Gap (FVG), Market Structure Shift (MSS), Liquidity Sweep, or Premium/Discount calculation.
- **Order Execution Invariant:** Live order execution remains strictly disabled (`LIVE_EXCHANGE_ORDERS_SENT = 0`).
- **Heartbeat & Telemetry:** Every watcher evaluation pulse emits an authoritative `canonical_quote` snapshot generated via `resolve_watcher_observation_provenance()`.
