# AURUM DESK V17.7.4.1 — PAPER PILOT READINESS & RISK GOVERNANCE

**Release Version:** V17.7.4.1  
**Target Branch:** `feat/v17-7-adaptive-structural-stop-loss`  
**Target Asset:** XAUUSDT (Perpetual / CFD / Futures)  
**Execution Mode Readiness:** `SHADOW_COMPARE` (Ready) \| `PAPER_STRUCTURAL` (Eligible for Controlled Opt-In)  
**Live Trading Status:** STRICTLY FORBIDDEN / ZERO LIVE ORDERS SENT  

---

## 1. Executive Summary

Aurum Desk V17.7.4.1 establishes engineering and quantitative readiness for a controlled **XAUUSDT Paper Pilot** utilizing the Adaptive Structural Stop Loss framework.

The release enforces three distinct operating modes:
1. **`LEGACY` (Default / Baseline):** Fixed or volatility-multiple stop loss calculations as used prior to V17.7.
2. **`SHADOW_COMPARE` (Production Non-Interfering):** Evaluates structural anchors and ATR provenance concurrently with legacy stops. Emits comprehensive comparison telemetry without executing structural stops.
3. **`PAPER_STRUCTURAL` (Opt-In Controlled Pilot):** Executes structural stops in paper broker simulation only when both structural anchor causality and financial eligibility (risk budget, margin, liquidation buffer) pass.

---

## 2. Invariants & Financial Safeguards

### 2.1 Distant Stop Loss (>4x ATR) Integrity
- **Principle:** A structurally valid stop loss far from entry (e.g. 5x, 8x, or 10x ATR) is not rejected merely because of distance.
- **Sizing Adjustment:** The risk budget ($R$) is fixed. When stop distance ($|P_{\text{entry}} - P_{\text{stop}}|$) increases, position quantity ($Q$) is reduced proportionately:
  $$Q = \frac{\text{Risk Budget}}{|P_{\text{entry}} - P_{\text{stop}}| + \text{Slippage} + \text{Fees}}$$
- **Rejection Integrity:** Distant stops that exceed liquidation thresholds or fail minimum Net R:R ($\ge 1.3$) are rejected for their actual financial reason, never masked by arbitrary distance limits.

### 2.2 Frozen Structural Stop Contract
Once an opportunity transitions from candidate evaluation to ARM:
$$\text{candidate.stop\_loss} == \text{stop\_decision.selected\_sl} == \text{risk\_calculation.stop\_loss} == \text{armed\_order.stop\_loss}$$
- Stops cannot be widened post-ARM.
- Fill quantity cannot exceed authorized ARMED quantity.
- Fill-time checks re-verify price slippage and spread invariants.

### 2.3 Strict Zero Live Order Boundary
All live order submission adapters are blocked in paper and shadow modes:
$$\text{LIVE\_ORDER\_DISPATCH\_ATTEMPTS} == 0$$
$$\text{LIVE\_ORDER\_DISPATCH\_SUCCESSES} == 0$$

---

## 3. Telemetry & Journaling Specification

During paper simulation and shadow evaluation, every opportunity emits the following telemetry schema:

| Telemetry Field | Description | Source |
|---|---|---|
| `timestamp` | Epoch millisecond timestamp of evaluation | Engine Clock |
| `model` | Strategy model ID (`CG01`, `M1_REV`, `M1_CONT`, etc.) | Model Evaluator |
| `opportunity_id` | Unique UUID for candidate | Orchestrator |
| `direction` | `LONG` or `SHORT` | Model Evaluator |
| `planned_entry` | Target entry price | Model Evaluator |
| `actual_entry` | Fill execution price | Paper Broker |
| `structural_anchor` | Price of swing high/low or FVG invalidation | Stop Placement Service |
| `raw_atr_m1` | Authoritative 1-minute ATR (raw, unmultiplied) | Stop Placement Service |
| `raw_atr_m5` | Authoritative 5-minute ATR (optional) | Stop Placement Service |
| `sl_distance` | $|P_{\text{entry}} - P_{\text{stop}}|$ | Stop Placement Service |
| `sl_distance_atr` | $\text{sl\_distance} / \text{raw\_atr\_m1}$ | Stop Placement Service |
| `legacy_sl` | Pre-V17.7 stop loss reference | Baseline Evaluator |
| `structural_sl` | Computed structural stop loss | Stop Placement Service |
| `selected_sl` | Executable stop loss selected by policy | Stop Placement Policy |
| `independent_tp` | Structural take profit target | Exit Evaluator |
| `gross_rr` | $|P_{\text{tp}} - P_{\text{entry}}| / |P_{\text{entry}} - P_{\text{stop}}|$ | Calculator |
| `net_rr` | Net Risk:Reward incorporating fees & slippage | Calculator |
| `risk_budget` | Max permitted USD loss for the trade | Risk Engine |
| `position_quantity`| Computed contract/token size | Calculator |
| `arm_result` | `ARMED` or rejection code | Orchestrator |
| `fill_result` | `FILLED` or expired/canceled | Paper Broker |
| `pnl_realized` | Net profit or loss in USD | Paper Broker |
| `rejection_reason` | Explicit fail-closed code if rejected | Policy / Risk Engine |

---

## 4. Controlled Pilot Activation Checklist

Before activating `PAPER_STRUCTURAL` in a staging/sandbox environment, operators must execute:

- [ ] **Acceptance Verification:** Confirm latest run in `docs/artifacts/` has verdict `ACCEPTED_FOR_PAPER_OPT_IN`.
- [ ] **Mode Configuration:** Set `stop_placement_v1.mode = "PAPER_STRUCTURAL"` in `system_config`.
- [ ] **Live Dispatch Check:** Verify `EXCHANGE_API_KEY` and order dispatch configurations are unset or disabled.
- [ ] **Feed Freshness:** Confirm market data WebSocket feeds provide continuous closed M1/M5 candles with latency $< 2000$ms.
- [ ] **Telemetry Ingestion:** Confirm telemetry listener is capturing `StopBufferResult` and `StopPlacementDecision` events.
- [ ] **Max Open Positions:** Verify portfolio guard limits concurrent active positions to 1.
