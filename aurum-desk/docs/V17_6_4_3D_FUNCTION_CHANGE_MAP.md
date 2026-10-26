# AURUM V17.6.4.3d — FUNCTION CHANGE MAP
**Version:** V17.6.4.3d  
**Baseline SHA:** `97bbc84d8b11a2920b6f4068c2bdc21d908711ef`  
**Branch:** `fix/v14-1-rr-flow`  

---

## 1. Production Code Invariant & Zero Strategy Drift
- **Strategy Code Changes:** `0` (Zero SMC/ICT strategy changes; MSS, FVG, Liquidity Sweep, Net R:R >= 1.5, Slippage, Fees, Risk Sizing, Session Restrictions all 100% invariant).
- **Schema Changes:** `0` (No DB schema migrations needed; existing SQLite tables used).
- **Core Production Flows Preserved:**
  - Trading Flow: `Bitget Feed -> Collector -> QuoteValidator -> CG01Scheduler -> Watcher -> SMC/ICT -> Risk -> PAPER Order ARM -> Fill/Close -> Journal -> PnL`
  - Observability Flow: `Verified Market Feed -> Canonical Quote Provenance -> Heartbeat -> Cycle -> Interval -> SQLite Commit -> Independent Readback -> Coverage -> API -> Dashboard`

---

## 2. Updated Tools & Test Files

| File | Type | Changes & Purpose | Callers / Dependents |
|---|---|---|---|
| `backend/tests/test_v17_6_4_3d_scheduler_durable_evidence.py` | New Test Suite | Implements SDE01–SDE16: Direct `evaluate_symbol_now` execution with independent Engine/Session readback of committed `EngineEvaluationCycleRecord` and `CG01TraceRecord`. | Gates G17, G20, G21 |
| `backend/tests/test_v17_6_4_3d_scheduler_run_loop.py` | New Test Suite | Implements SR01–SR12: Starts actual `CG01EvaluationScheduler.run()` async task, injects market events via `notify_market_change()`, verifies single-flight, event consumption, cycle persistence, and clean shutdown without leaked tasks. | Gate G23 |
| `backend/tests/test_v17_6_4_3d_live_order_boundary.py` | New Test Suite | Implements LOB01–LOB18: Active safety spy attached to real network/exchange order boundaries; executes real PAPER ARM, FILL (`PaperBroker.process_price_tick`), and CLOSE (`TradeLifecycleService.execute_close`), verifying PnL, fees, journal, and confirming 0 live order attempts. | Gate G39 |
| `backend/tests/test_v17_6_4_3d_observation_soak.py` | New Test Suite | Implements SOAK01–SOAK18: 27-step deterministic multi-event observation sequence under simulated clock covering connect, stale, disconnect, reconnect, epoch change, SQLite lock/retry, restart gap, coverage equation, and independent readback. | Gate G41 |
| `backend/tests/test_v17_6_4_3d_runner_contracts.py` | New Test Suite | Implements RC01–RC18: Hardens runner subprocess execution; tests valid/missing/corrupted JUnit, zero-test rejection, timeouts, exit codes, and structured report parsing. | Runner validation |
| `backend/tests/test_v17_6_4_3d_acceptance_integrity.py` | New Test Suite | Implements AI01–AI26: Tests authoritative `evaluate_release_acceptance()` across all 26 failure modes, including missing gates, unexpected skips, timeout, dirty worktree, and artifact tampering. | Gate G42 |
| `tools/run_v17_6_4_3d_operational_smoke.py` | New Smoke Script | Standalone operational smoke that fetches public Bitget market data (without private trading credentials), routes through scheduler, records telemetry, and confirms committed readback. | Gate G45 |
| `tools/run_v17_6_4_3d_acceptance.py` | New Master Runner | 46-gate master acceptance runner; executes G45 before G43/G44; validates structured JUnit and Playwright JSON; computes and validates SHA-256 hashes for all 46 gate artifacts; generates `manifest_v17_6_4_3d.json`. | Master Acceptance Entrypoint |
