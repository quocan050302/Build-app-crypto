# AURUM V17.6 — OBSERVABILITY ARCHITECTURE SPECIFICATION
## Autonomous Observation, Live Decision Intelligence & Forward Paper Monitoring

**Target Release:** V17.6  
**Repository:** `quocan050302/Build-app-crypto`  
**Workspace:** `aurum-desk/`  
**Baseline:** V17.5.9 Verified Clean Release (`9ecd8b7f1351246f6be31a238a508f6b89ef9225`)

---

## 1. Executive Summary & Objective

AURUM V17.6 establishes a production-grade, durable, truthful observability system for automated XAUUSDT paper trading. 

Prior releases validated SMC/ICT structural analysis, entry guards, frozen cost snapshots, and paper execution. However, an operator observing the application had difficulty answering vital operational questions:
1. Is the engine actually running or is only the web interface open?
2. When did the bot last receive valid market quotes and candle data?
3. When did the bot last complete an evaluation cycle?
4. Why is the bot currently not taking trades (NO_SETUP vs BLOCKED vs WAITING)?
5. Which conditions are currently being waited on for an active setup?
6. Which paper orders were armed, filled, and closed?
7. What is the verified Net Realized PnL after costs from committed transactions?
8. How much of the 1D / 7D / 14D observation window has trustworthy data coverage?
9. Did restarts or network disconnections cause unverified data gaps?

V17.6 provides definitive, verifiable, and non-misleading answers to these questions through a structured 3-tier observability model and a dedicated Observation Dashboard in the user interface.

---

## 2. Three-Tier Observability Architecture

The system strictly decouples three distinct levels of system status so that operational health is never conflated with trading decisions or financial performance:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       TIER 1: ENGINE OBSERVABILITY                          │
│ Backend Process · Worker Lease · Heartbeat Records · Market Feed Freshness  │
│ Multi-TF Candle Freshness · Evaluation Cycle Diagnostics · Recovery Status │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │ Feeds Verified Market & Cycle Data
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                  TIER 2: TRADING DECISION OBSERVABILITY                     │
│ SMC Evidence · Opportunity Transitions · Decision Event Timeline            │
│ Waiting Conditions (Missing vs Fulfilled) · Guard Block Categorization       │
│ Distinction: NO_SETUP (Clean Scan) vs BLOCKED (Candidate Rejected)          │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │ Produces Qualified / Armed Setups
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                 TIER 3: FINANCIAL & RESEARCH OBSERVABILITY                  │
│ Paper Orders · Armed / Filled / Closed Ledger · True Post-Fee Realized PnL  │
│ Drawdowns · Winrate (N/A when 0 closed) · Coverage Uptime · Session Metrics │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Tier 1: Engine Observability
- **Engine States:** `RUNNING`, `IDLE`, `WAITING_MARKET`, `PAUSED`, `DEGRADED`, `STALLED`, `STOPPED`, `UNKNOWN`.
  * `RUNNING`: Process is alive, lease is valid, evaluation cycle occurred within expected cadence.
  * `WAITING_MARKET`: Process is alive but market is closed or valid quote is absent.
  * `STALLED`: Process is alive but no cycle has completed within `cadence * 3` during open market.
  * `STOPPED`: Background worker has been intentionally stopped or terminated.
- **Heartbeat Recording:** Idempotent updates in SQLite table `engine_heartbeats` with distinct timestamps:
  * `last_process_heartbeat_at_ms`: Worker loop tick.
  * `last_evaluation_started_at_ms`: Cycle entry.
  * `last_evaluation_completed_at_ms`: Cycle exit.
  * `last_successful_evaluation_at_ms`: Cycle completed without transaction error.
- **Feed Freshness States:** `CONNECTED_FRESH`, `CONNECTED_STALE`, `DISCONNECTED`, `RECONNECTING`, `UNKNOWN`.
  * Tracks quote age from `item.ts` provider timestamp.
  * Tracks latest closed candle per timeframe (1M, 5M, 15M, 1H, 4H, D).
  * Gap detection (`detect_market_data_gaps`) aware of weekend market closures.

### Tier 2: Trading Decision Observability
- **Opportunity Funnel Progression:** Leverages existing `OpportunityFunnelRecord` and adds append-only `OpportunityDecisionEvent` history.
- **Standardized Stages:** `DETECTED`, `WATCHING`, `WAITING`, `QUALIFIED`, `BLOCKED`, `READY`, `ARMED`, `FILLED`, `EXPIRED`, `INVALIDATED`, `CLOSED`.
- **Reason Categorization:** Standard taxonomy:
  * `STRUCTURE`: Liquidity sweep unconfirmed, MSS incomplete, FVG invalidated, zone breached.
  * `MARKET_DATA`: Stale quote, candle gap, spread excessive.
  * `RISK`: Daily loss cap reached, max open positions reached, daily trade quota filled.
  * `NET_RR`: Net R:R below policy threshold after friction.
  * `NEWS`: High-impact news blackout window active.
  * `SESSION`: Current time outside eligible trading session.
  * `CAPITAL`: Insufficient equity for minimum position size.
  * `EXECUTION`: Concurrency lock collision, order submission error.
  * `CONFIGURATION`: Model or profile disabled.
  * `SYSTEM`: Database write failure, rollback triggered.
- **NO_SETUP vs BLOCKED Distinction:**
  * `NO_SETUP`: Evaluation cycle executed completely; SMC scanners detected no initial candidate structures.
  * `BLOCKED`: A candidate setup existed but a specific safety guard or policy prevented execution.

### Tier 3: Financial & Research Observability
- **Truthful Post-Fee Accounting:** Reads `realized_pnl_net` from committed `PaperOrder` records. Zero double fee deduction.
- **Zero Closed Trades Safeguard:** When `total_closed_trades == 0`, winrate and expectancy are reported as `N/A`, never `0.00%`.
- **Authoritative Equity Ledger:** Drawdown calculated from closed equity stream with initial capital baseline.
- **Observation Uptime Denominator:** Measured from verified observation intervals (`PaperObservationInterval`), excluding scheduled market closures. Unmonitored time is flagged as `UNVERIFIED_COVERAGE`.

---

## 3. Production Data Flow Diagram

```text
                           [ BITGET WS & REST FEED ]
                                      │
                         Canonical Quotes & Candles
                                      ▼
                           [ CANONICAL MARKET FEED ]
                      (BitgetWSService & CandleCacheService)
                                      │
                         Quote Freshness & Gap Health
                                      │
                                      ▼
                        [ SCALP WATCHER ORCHESTRATOR ]
                                      │
           ┌──────────────────────────┼──────────────────────────┐
           │                          │                          │
           ▼                          ▼                          ▼
   [ ENGINE HEARTBEAT ]     [ EVALUATION CYCLE ]       [ SMC OPPORTUNITY ]
 (engine_heartbeats table)  (Cycle diagnostic & stats) (Scalp candidate check)
           │                          │                          │
     State snapshot             Latency & status           Model decisions
           │                          │                          │
           └──────────────────────────┼──────────────────────────┘
                                      ▼
                         [ ENTRY GUARDS & ARBITRATION ]
                                      │
               ┌──────────────────────┴──────────────────────┐
               │                                             │
               ▼                                             ▼
     [ BLOCKED / WAITING ]                          [ QUALIFIED & ARMED ]
 (OpportunityDecisionEvent)                     (PaperOrder state: 'armed')
               │                                             │
               │                                             ▼
               │                                     [ PAPER EXECUTION ]
               │                                   (Fill on quote cross)
               │                                             │
               │                                             ▼
               │                                     [ TRADE LIFECYCLE ]
               │                                  (TP/SL, realized_pnl_net)
               │                                             │
               └──────────────────────┬──────────────────────┘
                                      ▼
                       [ OBSERVABILITY READ MODEL ]
                  (EngineObservabilityService & API layer)
                                      │
               ┌──────────────────────┴──────────────────────┐
               │                                             │
               ▼                                             ▼
     [ READ-ONLY REST API ]                        [ WEBSOCKET EVENTS ]
 (/api/v1/observation/*)                        (order.*, cycle.*, feed.*)
               │                                             │
               └──────────────────────┬──────────────────────┘
                                      ▼
                        [ OBSERVATION DASHBOARD ]
               (React UI tab: Cards, Funnel, Timeline, Reports)
```

---

## 4. API Contract & Endpoints

All endpoints are strictly read-only (`db.autoflush = False`), zero mutation, and perform range bounds validation:

1. `GET /api/v1/observation/status`:
   - Returns real-time health snapshot for Engine, Feed, SMC Evaluator, Paper Execution, and Active Run.
2. `GET /api/v1/observation/summary`:
   - Returns comprehensive observation summary: funnel progression, waiting conditions, blocked categories, financial metrics, and coverage uptime.
3. `GET /api/v1/observation/decisions`:
   - Returns paginated decision events with deterministic cursor ordering (`as_of_ms`, `event_id`).
4. `GET /api/v1/observation/runs/{run_id}`:
   - Returns specific observation run details, duration, coverage, linked orders, and health metrics.
5. `GET /api/v1/observation/runs/{run_id}/report`:
   - Returns server-generated structured Markdown report for the observation run.

---

## 5. UI Integration & Frontend Architecture

- **Tab Placement:** Added as `observation` tab ("Quan Sát Bot") adjacent to `chart` and `upcoming` in `App.tsx`.
- **Data Hook (`useObservationDashboard`):**
  * Bootstraps from `/api/v1/observation/summary` and `/status`.
  * Subscribes to real-time events via `wsClient`.
  * Fallback reconciliation polling (15-30s when active, backed off when tab hidden).
  * `AbortController` handles unmount and prevents race conditions.
- **Fail-Closed Presentation:**
  * Missing or unverified metrics render as `N/A`.
  * Equity fallback `|| '1,000.00'` replaced with verified status display.
  * Header version display synchronized to `V17.6`.
