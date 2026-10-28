# AURUM DESK V17.7.2 — SOURCE AND CALL GRAPH AUDIT
**Release:** V17.7.2 — Causal SL Evidence, True Runtime E2E & Fail-Closed Acceptance Repair  
**Branch:** `feat/v17-7-adaptive-structural-stop-loss`  
**Reference Base Commit:** `9aaaa5efc260f3009f905f5b709e2e0fe830ba32`  
**Date:** October 2026  
**Auditor:** Principal Quant Trading Systems Engineer & Release Safety Architect  

---

## 1. Executive Summary

AURUM DESK V17.7.1 introduced the non-negotiable architectural paradigm that structural Stop Losses are allowed to be distant (e.g. > 4.0× ATR) when supported by verified, causally confirmed structural anchors and financial risk constraints. However, direct source inspection revealed critical gaps in release acceptance, fail-closed volatility barriers, database isolation, network safety instrumentation, and test verifiability.

V17.7.2 delivers complete corrective repairs for all confirmed defects:
1. **Strict Fail-Closed Volatility Barrier (P0-01):** Missing, non-finite (NaN, Inf), non-positive, or unclosed M1 ATR under `PAPER_STRUCTURAL` mode deterministically produces `StopStatus.WAITING_VOLATILITY_EVIDENCE`, `selected_sl = None`, and `applied_variant = "NONE"`, completely preventing order arming.
2. **True Runtime Integration (P1-01 & P1-02):** Replaced mock/isolated component calls with true invocations of `evaluate_cg01_candidate()`, `ScalpM1ModelService` candidate evaluators, and the production orchestrator boundary `_arm_paper_candidate()`.
3. **Database Isolation & Durability (P0-04):** Implemented strict test database isolation via `assert_test_database_isolated()`, ensuring no pytest run can touch `aurum_desk.db` or shared runtime data.
4. **Active Live-Order Safety & Negative Control (P0-03):** Installed active HTTP interceptor spies across real candidate -> ARM -> fill -> close execution pathways, verifying 0 outbound exchange calls, and executed an active negative control test proving live-order attempts are intercepted and rejected.
5. **Multi-Threaded Idempotency Races (P1-03):** Replaced sequential duplicate tests with true concurrent thread races synchronized via `threading.Barrier(2)`.
6. **Canonical Quote & Causal Order Integrity (P1-06):** Removed inverted quote clipping (`max(0.0, ask - bid)`), strictly rejecting inverted quotes (`ask < bid`) and stale quotes up-front.
7. **Complete 23-Gate Acceptance Runner (P0-02):** Upgraded acceptance automation to execute all 23 release gates (G01 to G23) fail-closed, with exact commit SHA tracking and real machine-parsed metrics.

---

## 2. Authoritative Call Graph

The audited and verified production call graph for V17.7.2:

```text
Authoritative Market Data (WebSocket / REST Snapshot)
    ↓
Canonical Bid/Ask Quote & Freshness Verification
    [Rejects: bid<=0, ask<=0, ask<bid (INVERTED_QUOTE), stale quote]
    ↓
Validated Closed Candles (CanonicalCandle / Closed Context)
    [Strict cutoff: open_time + tf_duration <= as_of_ms]
    ↓
Model Candidate Evaluation
    ├─ CG01 Engine: evaluate_cg01_candidate()
    └─ Scalp M1 Models: evaluate_m1_*_candidate()
    ↓
Causally Bound Structural Anchor Resolution
    [Enforces: occurred_at <= known_at <= as_of_ms]
    [Validates: anchor not already breached by intervening closed bars]
    [Guarantees: anchor belongs to active structural episode]
    ↓
Required Volatility (ATR) Evidence Barrier
    [Enforces: M1 ATR AVAILABLE, finite, positive, >= 14 closed bars]
    [If missing under PAPER_STRUCTURAL: WAITING_VOLATILITY_EVIDENCE, sl=None]
    ↓
Structural Stop Loss Decision (plan_structural_stop)
    [Variant C: Anchor Price - Dynamic Protective Buffer]
    ↓
Independent Take Profit Resolution (resolve_executable_target)
    [TP derived from liquidity levels, independent of SL distance]
    ↓
Risk and Net R:R Calculator (domain_calculator.calculate_risk_reward)
    [Quantity floored to qty_step, Risk Budget strictly capped]
    [Net R:R >= min_net_rr (no artificial TP widening)]
    [Liquidation buffer & margin checks enforced]
    ↓
Candidate Eligibility Verification
    [cand.stop_loss == stop_placement.selected_sl == calc.stop_loss]
    ↓
Watcher / Scheduler Orchestrator (ScalpWatcherOrchestrator)
    ↓
Entry and Account Guards (EntryGuardService)
    [Session eligibility, news blackout, 1-position limit, daily fill cap, cooldown]
    ↓
Actual ARM Service (_arm_paper_candidate)
    [Freezes: stop_loss, take_profit, planned_entry, quantity, stop evidence]
    ↓
Persisted ARMED PaperOrder
    [Durably committed across independent database sessions]
    ↓
Execution Coordinator / Fill Evaluation
    [TradeLifecycleService.execute_fill]
    [Enforces: final_quantity <= armed_quantity (preserves or downsizes)]
    [No post-ARM Stop Loss widening permitted]
    ↓
PAPER_OPEN Position
    ↓
Shared Exit Evaluator (evaluate_order_exit)
    [LONG exit evaluated against executable Bid]
    [SHORT exit evaluated against executable Ask]
    ↓
CLOSED Position & Durability
    [Realized PnL net, exit cause, durably committed]
    ↓
Audit, Telegram Outbox & Research Journal Recording
```

---

## 3. Inspected Source Code Files

| File | Status | Key Verifications |
|---|---|---|
| `backend/services/stop_placement_types.py` | VERIFIED_FROM_SOURCE | Added `StopStatus.WAITING_VOLATILITY_EVIDENCE` enum member. |
| `backend/services/stop_placement_service.py` | VERIFIED_FROM_SOURCE | Added quote inversion guard, causal timestamps check, strict M1 ATR barrier under `PAPER_STRUCTURAL`. |
| `backend/services/cg01_context_adapter.py` | VERIFIED_FROM_SOURCE | Preserved raw spread (`ask - bid`), removed `max(0.0, ...)` clipping, marked inverted quotes invalid. |
| `backend/services/cg01_engine.py` | VERIFIED_FROM_SOURCE | Pre-flight quote validity, inversion, and staleness checks; passes verified ATR objects. |
| `backend/services/scalp_m1_model_service.py` | VERIFIED_FROM_SOURCE | Replaced spread clipping with `ask - bid`, passing verified ATR objects. |
| `backend/database.py` | VERIFIED_FROM_SOURCE | Enforced test DB isolation via `assert_test_database_isolated()`. |
| `backend/domain_calculator.py` | VERIFIED_FROM_SOURCE | Non-null geometry validation handling `None` gracefully without `TypeError`. |
| `backend/services/trade_lifecycle_service.py` | VERIFIED_FROM_SOURCE | Enforced `final_quantity <= armed_quantity` preservation/downsizing at fill boundary. |
| `backend/tests/test_v17_7_adaptive_stop_loss.py` | VERIFIED_FROM_TEST | Upgraded 14 gates to real integration and added 20 dedicated gates CE-01..CE-20 (total 100 tests). |
| `tools/run_v17_7_acceptance.py` | VERIFIED_FROM_SOURCE | Enforces all 23 release gates G01 to G23 fail-closed with exact tested commit SHA tracking. |

---

## 4. Verification Methodology Legend

- **VERIFIED_FROM_SOURCE:** Verified via comprehensive static inspection of active codebase.
- **VERIFIED_FROM_TEST:** Verified via automated execution in pytest or vitest test runner.
- **VERIFIED_FROM_PERSISTENCE:** Verified by committing transaction, closing session, and reading back in an independent session.
- **VERIFIED_FROM_REPLAY:** Verified across identical deterministic market data feed sequence.
- **VERIFIED_FROM_CI:** Verified within continuous integration pipeline.
