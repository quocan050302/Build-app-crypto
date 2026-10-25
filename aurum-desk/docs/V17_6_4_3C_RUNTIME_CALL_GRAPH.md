# AURUM V17.6.4.3c — RUNTIME CALL GRAPH & DATA PROVENANCE
**Version:** V17.6.4.3c  
**Baseline SHA:** `a08097e4cfab20f2a67b3a6ab71be46a3dd978ca`  
**Branch:** `fix/v14-1-rr-flow`  

---

## 1. Primary Market Data & Strategy Execution Call Graph

```text
Bitget Exchange WebSocket Feed
    │
    ▼
bitget_ws_service.py (BitgetWSService)
    ├─ Updates transport_state ("CONNECTED", "DISCONNECTED", "RECONNECTING", "DOWN")
    ├─ Increments connection_epoch on reconnect
    ├─ Captures exchange_ts_ms (from server) and received_at_ms (local receipt)
    └─ Emits ticker to CollectorService
           │
           ▼
collector_service.py (CollectorService)
    ├─ Caches latest_ticker
    ├─ Validates quote via QuoteValidator
    └─ Dispatches market event
           │
           ▼
cg01_scheduler_service.py (CG01EvaluationScheduler)
    ├─ notify_market_change(event)
    ├─ Single-flight check (_symbol_in_flight)
    ├─ Event deduplication (_seen_events)
    └─ evaluate_symbol_now(symbol, db, ...)
           │
           ▼
scalp_watcher_orchestrator.py (ScalpWatcherOrchestrator)
    ├─ resolve_watcher_observation_provenance(...)
    │     ├─ validate_observation_timestamp_authority()  [CPA-03: exch_ts + recv_ts required for GOOD]
    │     ├─ resolve_authoritative_transport_state()     [CPA-01: adapter transport overrides quote]
    │     ├─ Quote source authority check                [CPA-02: adapter WS required to authorize WS]
    │     └─ Connection epoch authority check            [CPA-04: quote epoch must match adapter epoch]
    ├─ evaluate_cycle(...)
    │     ├─ Evaluates SMC/ICT Setup (MSS, FVG, Liquidity Sweeps, Zone Discount/Premium)
    │     ├─ Validates Risk/Reward (Net R:R >= 1.5, Slippage, Fees)
    │     └─ PAPER Broker Order State Machine (evaluate_account_guard -> ARMED / FILLED)
    └─ Emits Heartbeat & Observation Interval
```

---

## 2. Observability & Durable Persistence Call Graph

```text
ObservationProvenance (from resolve_watcher_observation_provenance)
    │
    ▼
engine_observability_service.py (EngineObservabilityService)
    ├─ resolve_observation_evidence_quality()
    │     ├─ Symbol mismatch check -> UNVERIFIED
    │     ├─ Transport DISCONNECTED/DOWN -> DOWN
    │     ├─ Epoch mismatch / unverified -> DEGRADED
    │     ├─ Unknown transport -> UNVERIFIED
    │     ├─ Stale/Skewed timestamps -> STALE / DEGRADED
    │     └─ All verified -> GOOD
    └─ record_engine_heartbeat(db, ...)
           │
           ▼
observation_interval_service.py (ObservationIntervalService)
    ├─ record_heartbeat_event(run_id, symbol, ...)
    ├─ _persist_interval_record(db, interval_data, borrow_session=True)
    │     ├─ Unique conflict detection
    │     └─ Independent DB probe verification before returning ALREADY_EXISTS (CFP-07)
    └─ flush_active_intervals(db)
           │
           ▼
SQLite Database (PaperObservationInterval, CG01TraceRecord, EngineEvaluationCycleRecord)
    ├─ Committed atomically
    └─ Read back by Independent Connection Probes for Verification
```

---

## 3. Executive Dashboard Call Graph

```text
React Frontend (ObservationDashboard.tsx)
    │
    ▼ (HTTP GET /api/v1/observation/dashboard?symbol=XAUUSDT)
main.py -> get_observation_dashboard()
    │
    ▼
engine_observability_service.py -> build_observation_dashboard_snapshot()
    ├─ Checks feed_qual ("GOOD", "DEGRADED", "DOWN", "UNVERIFIED")
    ├─ Evaluates current_observation_evidence_health:
    │     ├─ If feed_qual == "GOOD" and running and intervals fresh -> "HEALTHY"
    │     ├─ If feed_qual in ("UNVERIFIED", None) -> "UNVERIFIED"
    │     └─ If down / stalled -> "DEGRADED" / "DOWN"
    └─ Returns Executive Health Cards & Telemetry Breakdown
```
