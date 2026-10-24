# AURUM V17.6 — IMPLEMENTATION MAP & FILE INVENTORY

**Release**: V17.6 — Autonomous Observation, Live Decision Intelligence & Forward Paper Monitoring  
**Target Symbol**: `XAUUSDT` Perpetual (Pure PAPER/SHADOW Trading)  
**Branch**: `fix/v14-1-rr-flow`  
**Baseline Git SHA**: `9ecd8b7f1351246f6be31a238a508f6b89ef9225` (V17.5.9 Clean Release)  
**Execution Environment**: macOS / Linux / Windows, Python 3.13 FastAPI, React 19 / TypeScript 6 / Vite 8  

---

## 1. Executive Summary

AURUM V17.6 introduces a complete, production-grade observability and decision intelligence architecture for autonomous XAUUSDT paper trading. It establishes an unshakeable bridge between background algorithmic processes and user visibility, answering all 20 critical operational questions with verifiable on-screen telemetry.

The implementation strictly maintains:
1. **Zero Modifications to Trading Logic**: Core SMC/ICT setup detection, entry guards, stop placement policies, and frozen cost models are 100% preserved.
2. **Strict Paper/Shadow Boundaries**: Zero live exchange orders, zero API key mutation.
3. **Fail-Closed Presentation**: Missing or uninitialized data renders as explicit `N/A`, never misleading placeholders like `$1,000.00` or `$0.00`.
4. **Three-Tier Architecture**:
   - **Tier 1**: Real-Time Engine Heartbeat, Evaluation Cycle Diagnostics, Feed Freshness, and Stall Detection.
   - **Tier 2**: Opportunity Funnel Tracking, Decision Timeline, Deduplication, and Waiting Condition Inspection.
   - **Tier 3**: Observation Run Ledger, Mathematical Coverage Calculation, Financial Aggregation, and Markdown Export.

---

## 2. File Inventory & Component Mapping

| Component / File Path | Status | Tier | Primary Responsibilities |
|---|---|---|---|
| `docs/V17_6_OBSERVABILITY_ARCHITECTURE.md` | Created | Docs | 3-tier architectural design, state machines, primary reason taxonomy, mathematical uptime formulation. |
| `docs/V17_6_IMPLEMENTATION_MAP.md` | Created | Docs | Technical blueprint, file registry, database schema specifications, and API mapping. |
| `docs/V17_6_OBSERVATION_USER_GUIDE.md` | Created | Docs | Comprehensive operator manual explaining executive cards, deep-dive panels, and decision audit workflows. |
| `docs/V17_6_FINAL_ACCEPTANCE_REPORT.md` | Created | Docs | Audited release evidence across all 16 technical gates, answering the 20 operational questions. |
| `backend/models.py` | Modified | Core/DB | Added SQLAlchemy models: `EngineHeartbeatRecord`, `OpportunityDecisionEvent`, `EngineIncidentRecord`. |
| `backend/database.py` | Modified | Core/DB | Added DDL schema migrations, compound indexes, and foreign keys for V17.6 tables. |
| `backend/migrate.py` | Modified | Core/DB | Added table initialization in `run_migration()`. |
| `backend/services/engine_observability_service.py` | Created | Service | Singleton service orchestrating heartbeats, stall detection, cycle diagnostics, decision timelines, and coverage calculations. |
| `backend/services/opportunity_funnel_service.py` | Modified | Service | Integrated `record_decision_event` into `record_opportunity_transition` with reason code mapping. |
| `backend/services/scalp_watcher_orchestrator.py` | Modified | Service | Integrated cycle IDs, monotonic performance timers, engine heartbeat recording, and evaluation cycle telemetry. |
| `backend/main.py` | Modified | API | Mounted read-only REST endpoints under `/api/v1/observation/*`. |
| `backend/tests/e2e_server.py` | Modified | Test | Added `/__e2e/seed_observation` endpoint for browser test bootstrapping. |
| `backend/tests/test_v17_6_engine_heartbeat.py` | Created | Test | Unit tests for heartbeat upsert, stall detection, cadence, and incident tracking. |
| `backend/tests/test_v17_6_market_freshness.py` | Created | Test | Unit tests for quote freshness, multi-timeframe candle staleness, and weekend market break detection. |
| `backend/tests/test_v17_6_cycle_telemetry.py` | Created | Test | Unit tests for evaluation cycle ring buffer, duration profiling, and diagnostic snapshots. |
| `backend/tests/test_v17_6_decision_timeline.py` | Created | Test | Unit tests for decision event recording, reason categorization, deduplication, and pagination. |
| `backend/tests/test_v17_6_observation_run.py` | Created | Test | Unit tests for observation coverage calculation, mathematical uptime, and markdown report generation. |
| `backend/tests/test_v17_6_financial_summary.py` | Created | Test | Unit tests for post-fee financial metrics extraction, fail-closed winrate, and paper ledger parity. |
| `backend/tests/test_v17_6_api_contract.py` | Created | Test | Unit tests verifying HTTP status codes, schema conformance, and query filter isolation. |
| `backend/tests/test_v17_6_recovery.py` | Created | Test | Unit tests verifying restart resilience, disconnect/reconnect tracking, and database crash recovery. |
| `backend/tests/test_v17_6_decision_parity.py` | Created | Test | Regression test proving V17.6 produces 100% identical trading orders to baseline V17.5.9. |
| `backend/tests/test_v17_6_soak.py` | Created | Test | 50-cycle deterministic soak test verifying memory stability and zero dropped cycles. |
| `frontend/src/types/observation.ts` | Created | UI Types | TypeScript interfaces for snapshots, coverage metrics, waiting setups, blocked reasons, and decision items. |
| `frontend/src/api/client.ts` | Modified | UI API | Client methods for `getObservationStatus`, `getObservationSummary`, `getObservationDecisions`, `getObservationRunReport`. |
| `frontend/src/hooks/useObservationDashboard.ts` | Created | UI Hook | React custom hook managing snapshot polling, WebSocket subscriptions, timezone toggles, and AbortController. |
| `frontend/src/components/ObservationDashboard.tsx` | Created | UI Component | Rich responsive dashboard rendering 5 executive health cards, funnel, waiting conditions, and decisions table. |
| `frontend/src/App.tsx` | Modified | UI App | Added `observation` tab, wired version badge to `V17.6`, replaced misleading fallback values with fail-closed `N/A`. |
| `frontend/src/__tests__/ObservationDashboard.test.tsx` | Created | UI Test | Unit test for executive card rendering, fail-closed winrate, waiting conditions, and decision timeline. |
| `frontend/src/__tests__/useObservationDashboard.test.tsx` | Created | UI Test | Unit test verifying hook bootstrapping, timezone toggling, and range adjustments. |
| `frontend/tests/e2e/v17_6_observation.e2e.ts` | Created | UI E2E | Playwright end-to-end browser verification of full navigation, health cards, panels, and table. |
| `tools/run_v17_6_observation_soak.py` | Created | Tool | Standalone CLI multi-cycle soak test runner with memory RSS tracking. |
| `tools/run_v17_6_acceptance.py` | Created | Tool | Authoritative cross-platform acceptance runner executing all 16 technical gates (G0 to G16). |

---

## 3. Database Schema Architecture

### 3.1. `engine_heartbeats` Table
Persists process liveness and evaluation progress independently:
```sql
CREATE TABLE IF NOT EXISTS engine_heartbeats (
    engine_instance_id VARCHAR(64) PRIMARY KEY,
    symbol VARCHAR(16) NOT NULL DEFAULT 'XAUUSDT',
    run_id VARCHAR(64),
    state VARCHAR(32) NOT NULL DEFAULT 'RUNNING',
    last_process_heartbeat_at_ms BIGINT NOT NULL,
    last_evaluation_started_at_ms BIGINT,
    last_evaluation_completed_at_ms BIGINT,
    last_successful_evaluation_at_ms BIGINT,
    last_cycle_id VARCHAR(64),
    cycles_count INTEGER NOT NULL DEFAULT 0,
    failed_cycles_count INTEGER NOT NULL DEFAULT 0,
    cadence_sec INTEGER NOT NULL DEFAULT 5,
    is_stalled BOOLEAN NOT NULL DEFAULT 0,
    stalled_reason TEXT,
    metadata_json TEXT,
    created_at_ms BIGINT NOT NULL,
    updated_at_ms BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_engine_hb_symbol_updated ON engine_heartbeats (symbol, updated_at_ms);
```

### 3.2. `opportunity_decision_events` Table
Audit trail of every algorithmic transition with deduplication and provenance:
```sql
CREATE TABLE IF NOT EXISTS opportunity_decision_events (
    event_id VARCHAR(64) PRIMARY KEY,
    opportunity_id VARCHAR(64) NOT NULL,
    symbol VARCHAR(16) NOT NULL DEFAULT 'XAUUSDT',
    model_id VARCHAR(64) NOT NULL,
    direction VARCHAR(8) NOT NULL,
    stage VARCHAR(32) NOT NULL,
    run_id VARCHAR(64),
    cycle_id VARCHAR(64),
    primary_reason_code VARCHAR(64) NOT NULL,
    reason_category VARCHAR(32) NOT NULL,
    reason_codes_json TEXT NOT NULL DEFAULT '[]',
    evidence_ids_json TEXT NOT NULL DEFAULT '[]',
    waiting_conditions_json TEXT,
    payload_json TEXT,
    occurred_at_ms BIGINT NOT NULL,
    is_duplicate_suppressed BOOLEAN NOT NULL DEFAULT 0,
    dedup_signature VARCHAR(128)
);
CREATE INDEX IF NOT EXISTS idx_dec_events_opp_time ON opportunity_decision_events (opportunity_id, occurred_at_ms);
CREATE INDEX IF NOT EXISTS idx_dec_events_run_time ON opportunity_decision_events (run_id, occurred_at_ms);
CREATE INDEX IF NOT EXISTS idx_dec_events_category ON opportunity_decision_events (reason_category, occurred_at_ms);
CREATE INDEX IF NOT EXISTS idx_dec_events_dedup ON opportunity_decision_events (dedup_signature, occurred_at_ms);
```

### 3.3. `engine_incidents` Table
Tracks operational interruptions, stalls, feed disconnections, and recoveries:
```sql
CREATE TABLE IF NOT EXISTS engine_incidents (
    incident_id VARCHAR(64) PRIMARY KEY,
    engine_instance_id VARCHAR(64),
    symbol VARCHAR(16) NOT NULL DEFAULT 'XAUUSDT',
    incident_type VARCHAR(32) NOT NULL,
    severity VARCHAR(16) NOT NULL DEFAULT 'WARNING',
    message TEXT NOT NULL,
    details_json TEXT,
    started_at_ms BIGINT NOT NULL,
    resolved_at_ms BIGINT,
    is_resolved BOOLEAN NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_incidents_active ON engine_incidents (symbol, is_resolved, started_at_ms);
```

---

## 4. REST API Endpoint Mapping

All endpoints are mounted on the FastAPI application under `/api/v1/observation/*` and are strictly read-only:

| Method | Endpoint | Query Parameters | Response Model | Description |
|---|---|---|---|---|
| `GET` | `/api/v1/observation/status` | `symbol: str = "XAUUSDT"`<br>`now_ms: Optional[int] = None` | Engine + Feed Status | Returns immediate liveness, stall status, feed freshness, and latest cycle diagnostic. |
| `GET` | `/api/v1/observation/summary` | `symbol: str = "XAUUSDT"`<br>`scope: str = "CG01"`<br>`run_id: Optional[str] = None`<br>`start_ms: Optional[int] = None`<br>`end_ms: Optional[int] = None` | `ObservationDashboardSnapshot` | Aggregated dashboard snapshot containing 5 health cards, funnel, waiting conditions, blocked categories, financial summary, and recent decisions. |
| `GET` | `/api/v1/observation/decisions` | `symbol: str = "XAUUSDT"`<br>`run_id: Optional[str] = None`<br>`stage: Optional[str] = None`<br>`category: Optional[str] = None`<br>`limit: int = 50`<br>`cursor: Optional[str] = None` | `DecisionsListPage` | Keyset-paginated decision event timeline with localized Vietnamese descriptions and technical reasons. |
| `GET` | `/api/v1/observation/runs/{run_id}` | `run_id: str` (path) | Run Metrics + Coverage | Detailed run record with mathematical uptime coverage, order links, and cycle statistics. |
| `GET` | `/api/v1/observation/runs/{run_id}/report` | `run_id: str` (path) | `text/markdown` | Generates downloadable comprehensive Markdown audit report with executive summaries and incident logs. |

---

## 5. Architectural Invariants & Failure-Mode Defenses

1. **Separation of Heartbeat vs Cycle Execution**:
   - `last_process_heartbeat_at_ms` updates every cycle initiation or background tick.
   - `last_evaluation_completed_at_ms` updates ONLY when an evaluation cycle finishes.
   - If an evaluation cycle hangs inside an external call, heartbeat age stays low while cycle age grows, allowing instant stall identification without false process restarts.
2. **Weekend Market Break Awareness**:
   - Gold (XAUUSDT) trading halts from Friday 17:00 NY to Sunday 18:00 NY.
   - During this interval, lack of quotes is classified as `WAITING_MARKET` instead of `DEGRADED` or `STALLED`.
3. **Fail-Closed Financial Reporting**:
   - Winrate is mathematically defined as `wins / closed_trades`. If `closed_trades == 0`, winrate is `null` (`None`) and UI renders `N/A (0 lệnh)` instead of `0.0%` or `100.0%`.
4. **Deduplication of Non-Informative Events**:
   - Frequent identical `NO_SETUP` evaluations within the same candle minute are suppressed using `dedup_signature = hash(opp_id + stage + reason + minute)`.
   - Distinct state transitions (e.g. `ZONE_DETECTED` -> `TRIGGER_CONFIRMED` -> `ARMED`) are never suppressed.
