# AURUM V17.6.4.3b — RUNTIME CALL GRAPH & DATA FLOW ARCHITECTURE
**Version:** V17.6.4.3b  
**Scope:** Feed Provenance, Scheduler Pipeline, Observability, and Transaction Durability Call Graphs  
**Architect:** Principal Quantitative Systems Engineer  

---

## 1. Feed-to-Cycle Call Graph

```mermaid
sequenceDiagram
    autonumber
    actor Exchange as Bitget WebSocket
    participant BWS as BitgetWSService
    participant SPO as ScalpWatcherOrchestrator
    participant SCHED as CG01EvaluationScheduler
    participant OBS as EngineObservabilityService
    participant INT as ObservationIntervalService
    participant DB as SQLite DB

    Exchange->>BWS: on_message(raw_ticker_payload)
    BWS->>BWS: update latest_quote (CanonicalQuote)
    
    rect rgb(240, 245, 255)
    Note over SPO,BWS: Phase 1: Provenance Resolution (CFP-01 - CFP-04)
    SPO->>BWS: read transport_state & latest_quote
    SPO->>SPO: resolve_watcher_observation_provenance()
    Note right of SPO: Exchange TS, Received TS & Source strictly validated
    end

    rect rgb(255, 245, 240)
    Note over SPO,INT: Phase 2: Heartbeat & Interval Recording
    SPO->>OBS: record_engine_heartbeat(provenance.to_snapshot_dict())
    OBS->>INT: record_heartbeat_event()
    INT->>INT: check active interval duration >= 60s
    alt Window Sealed
        INT->>INT: seal_active_interval() -> SEALED_PENDING
        INT->>DB: persist_pending_intervals()
        alt Borrowed Session Collision (CFP-07)
            INT->>DB: probe committed duplicate independently
            alt Committed Confirmed
                INT-->>INT: ALREADY_EXISTS (evict)
            else Uncommitted / Probe Missing
                INT-->>INT: RETRYABLE_FAILURE (retain in queue)
            end
        end
    end
    end

    rect rgb(245, 255, 240)
    Note over SCHED,DB: Phase 3: Event-Driven Evaluation (CFP-05)
    Exchange->>SCHED: notify_market_change(ev)
    SCHED->>SCHED: evaluate_symbol_now()
    SCHED->>SCHED: single-flight check & lock
    SCHED->>SCHED: capture_cg01_input_bundle()
    SCHED->>SCHED: capture_cg01_quote_snapshot()
    SCHED->>DB: record_evaluation_cycle()
    SCHED->>SCHED: release flight lock
    end

    rect rgb(255, 250, 240)
    Note over OBS,DB: Phase 4: Truthful Dashboard Observability (CFP-06, CFP-08)
    OBS->>OBS: build_observation_dashboard_snapshot()
    OBS->>OBS: check feed_qual == "GOOD"
    alt Feed Unverified / Missing
        OBS-->>OBS: current_observation_evidence_health = "UNVERIFIED"
    else Feed Good + Cycle Fresh + Interval Committed
        OBS-->>OBS: current_observation_evidence_health = "HEALTHY"
    end
    end
```

---

## 2. Component Responsibility Matrix

| Component | Responsibility | Fault-Isolation Guarantees |
|---|---|---|
| `BitgetWSService` | Raw transport, ping/pong, heartbeat, auto-reconnect, connection epoch | Disconnected socket never reports connected |
| `ScalpWatcherOrchestrator` | Watcher execution loop, provenance resolution, strategy cycle evaluation | Fails closed on missing timestamps or unknown source |
| `CG01EvaluationScheduler` | Event-driven candle change evaluation, single-flight per symbol, input bundling | Deterministic clock, shadow mode isolation, clean lock release |
| `ObservationIntervalService` | Rolling 60s window tracking, sealing, multi-DB pending queue, readback durability probe | Uncommitted duplicate remains retried; never prematurely evicted |
| `EngineObservabilityService` | Multi-tier health snapshots, coverage calculations, audit trail reporting | Feed quality gating: UNVERIFIED feed cannot produce HEALTHY status |
