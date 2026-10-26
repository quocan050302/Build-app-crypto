# AURUM DESK V17.7.1 — STRUCTURAL SL RUNTIME AUDIT
**Role:** Principal Quant Trading Systems Engineer, Senior Python Architect, Risk Management Auditor  
**Date:** October 2026  
**Branch:** `feat/v17-7-adaptive-structural-stop-loss`  
**Version:** V17.7.1  

---

## 1. Executive Summary & Runtime Audit Scope

AURUM DESK V17.7 introduced adaptive structural stop placement with dynamic volatility buffers, minimum protective distance floors, and outward expansion. However, a comprehensive source-level and runtime integrity audit revealed critical defects:
1. **Arbitrary 4.0 × ATR Distance Hard Rejection:** In `plan_structural_stop()`, stops where distance exceeded `4.0 * atr_m1` were automatically rejected with `REJECTED_GEOMETRY`, violating the fundamental principle that ATR measures volatility, not structural invalidation authority.
2. **Superficial Test Assertions:** Several tests in `test_v17_7_adaptive_stop_loss.py` previously asserted mock local variables or constants (e.g. SL-50 asserting `LIVE_EXCHANGE_ORDERS_SENT = 0; assert LIVE_EXCHANGE_ORDERS_SENT == 0` without instrumenting actual network boundaries, SL-41 only asserting a math formula without calling `DailyNYResearchService`).
3. **PaperOrder Database Schema Incompatibility:** Stop loss metadata was attempted to be passed as `bound_stop_decision` rather than through the production `cost_snapshot` JSON column (`{"stop_placement": ...}`).
4. **CG01 Fallback ATR Invention:** In `cg01_engine.py`, when ATR was missing, fallback logic previously invented a 1.0 ATR value instead of properly failing closed with explicit evidence states.
5. **Acceptance Runner Loopholes:** `tools/run_v17_7_acceptance.py` unconditionally output `ACCEPTED_FOR_PAPER_OPT_IN` without executing full regression matrices, parsing actual exit codes, or capturing exact test counts.

V17.7.1 corrects all identified defects with verifiable production-grade implementations and authentic test evidence.

---

## 2. End-to-End Pipeline Function Trace

The authoritative runtime pipeline was traced across all modified components:

```
[Market Feed]
    ↓ (WebSocket / REST Ticker)
[Canonical Quote Validation] (Bid, Ask, Spread checks, max_spread_usd barrier)
    ↓
[Closed Candles M1 / M5] (Strict causality, no lookahead, closed_candles_at)
    ↓
[SMC / ICT Pattern Engine] (MSS, Swings, Sweeps, FVGs)
    ↓
[Structural Anchor Resolution] (resolve_stop_anchor: confirmed pivot/extreme)
    ↓
[ATR & Spread Evidence Verification] (compute_stop_atr, fail-closed on missing M1)
    ↓
[Structural SL Planning] (plan_structural_stop: anchor + dynamic buffer + min protective distance)
    ↓
[Evidence-Based Distance Assessment] (NORMAL, ELEVATED, EXTREME, ATR_UNAVAILABLE - non-blocking)
    ↓
[Authoritative Risk & Net R:R Geometry] (domain_calculator.calculate_risk_reward)
    ↓
[Candidate Selection & Policy Barrier] (validate_applied_stop_decision)
    ↓
[ARM State & Order Freezing] (PaperOrder persisted with frozen stop_loss & cost_snapshot)
    ↓
[Execution Coordinator / Fill-Time Recheck] (Frozen SL invariant, downsized quantity on adverse entry)
    ↓
[PAPER Broker Fill] (execute_fill: state=paper_open, actual_entry, frozen SL)
    ↓
[Shared Exit Evaluator] (evaluate_order_exit: bid touches SL for LONG, ask touches SL for SHORT)
    ↓
[Order Closure & Persistence] (execute_close: state=closed, actual_exit, realized_pnl_net)
    ↓
[Evidence Logging & Outbox Integration] (cost_snapshot stop_placement payload, NotificationOutbox)
```

---

## 3. Verified Defect Investigations & Remediation

### 3.1 Defect D-01: 4.0 × ATR Distance Hard Rejection
- **Location:** `backend/services/stop_placement_service.py` (`plan_structural_stop`).
- **Root Cause:** Enforcing `if final_dist > max_allowed_dist: return REJECTED_GEOMETRY`.
- **Remediation:** Removed the hard rejection. Replaced with non-blocking diagnostic classification (`DISTANCE_NORMAL`, `DISTANCE_ELEVATED`, `DISTANCE_EXTREME`, `DISTANCE_ATR_UNAVAILABLE`). Legitimate distant stops (e.g. 7.5x ATR) are now accepted if structural invalidation and financial checks pass.
- **Verification:** Unit test `SL-20` and dedicated gates `DSL-01`, `DSL-02`, `DSL-15` verify that stops exceeding 4x ATR pass with `VALID` and `DISTANCE_EXTREME`.

### 3.2 Defect D-02: Zero Live Order Network Boundary Mocking
- **Location:** `backend/tests/test_v17_7_adaptive_stop_loss.py` (`test_sl_50`).
- **Root Cause:** Test only verified a local integer variable without instrumenting actual outbound HTTP clients.
- **Remediation:** Installed active mock spies on `httpx.Client.post` and `httpx.AsyncClient.post`, asserting `spy.call_count == 0` during all trading operations, and verified that `PaperBroker` contains zero exchange order dispatch methods.
- **Verification:** `SL-50` and `DSL-19` pass with 100% boundary instrumentation.

### 3.3 Defect D-03: Model Integration Superficiality
- **Location:** `backend/tests/test_v17_7_adaptive_stop_loss.py` (`test_sl_36`..`test_sl_41`).
- **Root Cause:** Assertions only checked `result is not None` or local dict keys.
- **Remediation:** Rewrote all model gates to invoke genuine model resolution boundaries (`resolve_stop_anchor`, `build_sweep_stop_descriptor`, `DailyNYResearchService.evaluate_daily_research_candidate`) with actual closed-candle fixtures and verified geometry.
- **Verification:** `SL-36` through `SL-41` pass against real engine logic.

### 3.4 Defect D-04: Full Persisted Lifecycle Omission
- **Location:** `backend/tests/test_v17_7_adaptive_stop_loss.py` (`test_sl_60`).
- **Root Cause:** Test used simulated mock dicts rather than persisting to the test database and running `execute_fill` and `execute_close`.
- **Remediation:** Rebuilt `SL-60` and `DSL-16` to execute real SQLAlchemy database transactions: CANDIDATE -> ARMED order -> `execute_fill` -> PAPER_OPEN -> `evaluate_order_exit` -> `execute_close` -> CLOSED -> persisted evidence verification.
- **Verification:** Both `SL-60` and `DSL-16` pass with full SQLite transaction verification.

---

## 4. Financial Safeguards & Invariants

Removing the arbitrary 4x ATR distance limit does **not** increase risk exposure:
1. **Risk Budget Invariant:** Risk per trade remains strictly capped at configured risk percent (e.g. 0.05% or 0.25% of capital).
2. **Quantity Downscaling:** As stop distance $d$ increases, quantity $Q$ decreases proportionally:
   $$Q = \frac{\text{Risk Budget}}{\text{Risk Per Unit}} = \frac{\text{Capital} \times \text{Risk \%}}{(d + \text{Fees} + \text{Slippage}) \times \text{Multiplier}}$$
3. **Minimum Quantity Barrier:** If $Q < 0.01$ oz (minimum contract size), the trade is rejected with `MIN_QTY_EXCEEDS_BUDGET` (verified in `DSL-03`).
4. **Net R:R Barrier:** Distant stops require larger target distances to achieve the mandatory minimum Net R:R ($\ge 1.5$ or $2.0$). If independent liquidity does not support the required TP, the trade is rejected with `REJECTED_WITH_NET_RR` (verified in `DSL-04`, `DSL-11`).
5. **Liquidation & Margin Barrier:** Distant stops that would place the stop beyond the estimated liquidation boundary are rejected (verified in `DSL-05`).
