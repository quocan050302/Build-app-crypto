# AURUM DESK V17.7 — SOURCE AUDIT & ROOT CAUSE INVESTIGATION
**Author:** Principal Quant Trading Systems Engineer & Risk Auditor
**Scope:** Source Code Analysis, Empirical Defect Verification & Order Lifecycles
**Date:** October 2026

---

## 1. Source Inventory & Baseline Inspection

### 1.1 Core Files Inspected
| File Path | Role in Stop Placement Pipeline |
|---|---|
| `backend/services/stop_placement_types.py` | Dataclasses, Enums, Policy parameters, Decision containers |
| `backend/services/stop_placement_service.py` | ATR calculation, dynamic buffer, protective distance, anchor resolution, quantization |
| `backend/services/stop_placement_policy_service.py` | SystemConfig persistence, validation, atomic CAS updates |
| `backend/services/stop_placement_research_service.py` | Paired comparison plans (Variants A, B, C), MAE/MFE excursions, lifecycle gates |
| `backend/services/scalp_m1_model_service.py` | M1 Reversal, Continuation, FVG, Sweep Reclaim models & stop application |
| `backend/services/cg01_engine.py` | CG01 Sweep + MSS + FVG evaluator, candidate generation, structural stop binding |
| `backend/services/scalp_watcher_orchestrator.py` | Candidate cycle evaluation, stop validation at ARM, snapshot freezing |
| `backend/services/execution_coordinator.py` | Order fill evaluation, fill-time risk recalculation, quantity preservation |
| `backend/domain_calculator.py` | Risk/reward calculation, position sizing, fees, slippage, liquidation |
| `backend/services/shared_exit_evaluator.py` | Bid/Ask exit triggering, gap pricing, stop execution |

---

## 2. Root Cause Classifications & Evidence

### Root Cause 1: LEGACY_STOP_TOO_TIGHT
- **Defect Location:** `cg01_engine.py` (legacy path) and `stop_placement_research_service.py` (line 196: `buffer_price = 0.05`).
- **Mechanism:** In legacy setups, stop distance was computed as:
  $$\text{SL} = \text{Anchor} \pm 5 \times 0.01 = \text{Anchor} \pm \$0.05 \text{ USD}$$
- **Evidence:** On XAUUSDT, the median bid/ask spread during active sessions is $0.15 - $0.25 USD. Placing a stop $0.05 USD beyond the anchor means the stop sits well inside the executable spread. Normal spread oscillation triggers the stop immediately without any meaningful price invalidation.
- **Classification:** `VERIFIED_FROM_SOURCE` and `VERIFIED_FROM_TEST` (SL-01, SL-04).

### Root Cause 2: STRUCTURAL_ANCHOR_TOO_CLOSE_TO_ENTRY
- **Defect Location:** `plan_structural_stop` prior to V17.7.
- **Mechanism:** Even if the anchor buffer was $0.20 USD, if the setup entered immediately after a shallow pullback near the swing extreme, the distance between planned entry and the anchor was tiny ($0.05 - $0.15 USD). The resulting entry-to-stop distance was under $0.35 USD ($3.50 gold ticks).
- **Impact:** Trades were stopped out on normal 1-minute candle noise before the market even tested structural liquidity.
- **V17.7 Fix:** Introduced `compute_min_protective_distance` which checks the entry-to-stop distance against M1 ATR, M5 ATR, and spread, expanding outward defensively if needed while preserving structural invalidation.
- **Classification:** `VERIFIED_FROM_SOURCE` and `VERIFIED_FROM_TEST` (SL-19).

### Root Cause 3: VARIANT_B_HARDCODED_BUFFER_ASSUMPTION
- **Defect Location:** `backend/services/stop_placement_research_service.py` line 216:
  `tick_floor_buf = 5.0 * tick_size`
- **Mechanism:** Variant B was hardcoded to 5 ticks regardless of policy `min_buffer_ticks`.
- **V17.7 Fix:** Variant B now dynamically reads `policy_settings.get("min_buffer_ticks", 5.0) * tick_size`.
- **Classification:** `VERIFIED_FROM_SOURCE`.

### Root Cause 4: MISSING_VOLATILITY_PROPAGATION_IN_CG01
- **Defect Location:** `cg01_engine.py` line 1400.
- **Mechanism:** When calling `plan_structural_stop`, `cg01_engine.py` only passed `atr_m1_val` and omitted `atr_m5_val` and `spread`.
- **Impact:** In CG01, the stop buffer was forced to default to M1 ATR only, ignoring macro M5 volatility and real-time spread conditions.
- **V17.7 Fix:** Updated `cg01_engine.py` to pass `atr_m5_val` and `spread=max(0.0, ask - bid)`.
- **Classification:** `VERIFIED_FROM_SOURCE` and `VERIFIED_FROM_TEST` (SL-36).

### Root Cause 5: LACK_OF_ENTRY_SPREAD_SHOCK_PROTECTION
- **Defect Location:** `plan_structural_stop` prior to V17.7.
- **Mechanism:** During high-impact news spikes or illiquid periods, spread can expand to $3.00 - $10.00 USD. Placing stops during such periods resulted in massive adverse fills or immediate stop triggers.
- **V17.7 Fix:** Added explicit spread verification: if spread exceeds `policy.max_spread_usd` (default 2.0 USD) or is negative/non-finite, the stop decision immediately returns `REJECTED_GEOMETRY` with `EXCESSIVE_SPREAD` or `INVALID_SPREAD`.
- **Classification:** `VERIFIED_FROM_SOURCE` and `VERIFIED_FROM_TEST` (SL-13, SL-14).

---

## 3. Order Lifecycle Audit & Safety Guarantees

### 3.1 Historical Orders Immutability
- All existing orders in `paper_orders` maintain their original `stop_loss`, `cost_snapshot`, and execution history.
- The V17.7 stop engine applies strictly to newly evaluated candidates. Historical orders queried via `/api/v1/orders/{order_id}/stop-evidence` return `UNAVAILABLE_LEGACY` cleanly if no structural evidence was recorded.

### 3.2 Candidate Sizing Atomicity
- In `scalp_m1_model_service.py` and `cg01_engine.py`, the stop loss used for `calculate_risk_reward` is strictly identical to `stop_dec.selected_sl`.
- In `scalp_watcher_orchestrator.py` at the ARM boundary, `STOP_PLAN_MISMATCH` verifies that `cand.stop_loss == calc.stop_loss`.
- `validate_applied_stop_decision` enforces that in `PAPER_STRUCTURAL` mode, `cand.stop_loss == stop_dec.selected_sl` within tick tolerance.

### 3.3 Zero Live Exchange Orders Invariant
- AST audit and source inspection of `paper_broker.py`, `execution_coordinator.py`, and `trade_lifecycle_service.py` confirm that all orders route to internal SQLite paper tables.
- Verification variable `LIVE_EXCHANGE_ORDERS_SENT = 0` was maintained across all test executions.
