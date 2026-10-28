# AURUM DESK V17.7 — ADAPTIVE STRUCTURAL STOP LOSS & VOLATILITY PROTECTION DESIGN
**Architecture & Implementation Specification**
**Author:** Principal Quant Trading Systems Engineer & Risk Engine Auditor
**Scope:** PAPER Trading, Volatility & Structural Stop Placement, Financial Integrity
**Date:** October 2026

---

## 1. Executive Summary & Problem Formulation

### 1.1 The Legacy Vulnerability
Prior to V17.7, Stop Loss (SL) placement on XAUUSDT (Gold) within SMC/ICT models relied on an anchor with a legacy tick buffer:
$$\text{SL}_{\text{legacy}} = \text{Anchor} \pm (\text{min\_buffer\_ticks} \times \text{tick\_size})$$
With `min_buffer_ticks = 5` and `tick_size = 0.01`, this created a buffer of only **$0.05 USD**.
On XAUUSDT, where:
- Typical bid/ask spreads range from **$0.10 to $0.35 USD** during active New York / London sessions,
- 1-minute Average True Range ($\text{ATR}_{M1}$) commonly fluctuates between **$1.00 and $3.50 USD**,
- Microstructure noise and sub-second order book imbalances routinely exceed $0.15 USD,

a $0.05 USD stop buffer meant that even if the macro structural invalidation level was sound, normal bid/ask bounces and execution spread immediately stopped out valid trades before market structure was truly invalidated.

Furthermore, when the planned entry price was close to the structural anchor (e.g. entry after shallow retracement or breakout near the swing extreme), the entry-to-stop distance was dangerously narrow ($0.10 - $0.30 USD), leading to premature stop-outs from mere quote volatility.

### 1.2 Core Architectural Principles of V17.7
1. **Never Blindly Widen Stops:** A stop loss is only moved outward when it remains defensible by structural invalidation and volatility evidence. Arbitrary widening is prohibited.
2. **Structural Invalidation as Strategy Foundation:** The stop must be anchored to proven, causal market structure (protected swing highs/lows, origin extremes, or swept liquidity points).
3. **Dual Dynamic Buffering:**
   - **Anchor-Relative Buffer:** Beyond the structural anchor based on M1 ATR, M5 ATR, and current spread.
   - **Entry-Relative Protective Distance:** A separate lower-bound threshold ensuring the entry-to-stop distance accommodates microstructure spread, execution slippage, and volatility noise.
4. **Strict Financial Risk Invariance:** Sizing quantity must decrease when stop distance increases so that total dollar loss remains strictly bounded by the risk budget:
   $$\text{Quantity} \times (\text{Stop Distance} + \text{Slippage} + \text{Fees}) \le \text{Risk Budget}$$
5. **No Artificial Take-Profit Inflation:** Take profit must remain anchored to independently verified liquidity targets. Moving TP further away to artificially "repair" Net R:R is strictly prohibited.
6. **Fail-Closed Execution Gate:** If reliable structural or volatility evidence is missing, or if risk constraints are violated, the opportunity is rejected with an explicit diagnostic code.

---

## 2. Mathematical Specification of V17.7 Adaptive SL

### 2.1 Component 1: Structural Anchor Resolution
For an opportunity evaluated at timestamp $T_{\text{eval}}$:
- **LONG Setup:** The structural anchor is the protected swing low ($P_{\text{low}}$), sweep extreme, or leg origin identified from closed candles strictly before $T_{\text{eval}}$:
  $$P_{\text{anchor}} \le P_{\text{entry}}$$
- **SHORT Setup:** The structural anchor is the protected swing high ($P_{\text{high}}$), sweep extreme, or leg origin:
  $$P_{\text{anchor}} \ge P_{\text{entry}}$$

If $P_{\text{anchor}}$ is missing or violates the directional inequality, the engine returns `WAITING_EVIDENCE` or `REJECTED_GEOMETRY` (`INVALID_ANCHOR_RELATION`).

### 2.2 Component 2: Dynamic Anchor Buffer ($\Delta_{\text{anchor}}$)
$$\Delta_{\text{anchor}} = \max \left(
    k_{\text{tick\_floor}} \times \text{tick\_size}, \;
    k_{M1} \times \text{ATR}_{M1}, \;
    k_{M5} \times \text{ATR}_{M5}, \;
    k_{\text{spread}} \times \text{Spread}
\right)$$
Where default policy settings are:
- $k_{\text{tick\_floor}} = 10 \text{ ticks}$ ($0.10 \text{ USD}$)
- $k_{M1} = 0.25$
- $k_{M5} = 0.10$
- $k_{\text{spread}} = 1.50$

The raw anchor-relative stop price is:
$$\text{SL}_{\text{anchor\_raw}} = \begin{cases}
    P_{\text{anchor}} - \Delta_{\text{anchor}} & \text{for LONG} \\
    P_{\text{anchor}} + \Delta_{\text{anchor}} & \text{for SHORT}
\end{cases}$$

### 2.3 Component 3: Minimum Entry-Relative Protective Distance ($D_{\text{min\_prot}}$)
To prevent stops from sitting inside normal microstructure noise when entry is very close to the anchor:
$$D_{\text{min\_prot}} = \max \left(
    \text{min\_protective\_ticks} \times \text{tick\_size}, \;
    k_{\text{prot\_M1}} \times \text{ATR}_{M1}, \;
    k_{\text{prot\_M5}} \times \text{ATR}_{M5}, \;
    k_{\text{prot\_spread}} \times \text{Spread}
\right)$$
Where default calibrated settings are:
- $\text{min\_protective\_ticks} = 10 \text{ ticks}$ ($0.10 \text{ USD}$)
- $k_{\text{prot\_M1}} = 0.50$
- $k_{\text{prot\_M5}} = 0.20$
- $k_{\text{prot\_spread}} = 2.00$

### 2.4 Component 4: Outward Expansion Logic
Let the preliminary entry-to-stop distance be:
$$D_{\text{entry}} = |P_{\text{entry}} - \text{SL}_{\text{anchor\_raw}}|$$

If $D_{\text{entry}} < D_{\text{min\_prot}}$:
- The stop is expanded outward to satisfy the protective threshold:
  $$\text{SL}_{\text{expanded}} = \begin{cases}
      P_{\text{entry}} - D_{\text{min\_prot}} & \text{for LONG} \\
      P_{\text{entry}} + D_{\text{min\_prot}} & \text{for SHORT}
  \end{cases}$$
- **Structural Guard:** In LONG, $\text{SL}_{\text{expanded}}$ is only adopted if $\text{SL}_{\text{expanded}} \le P_{\text{anchor}}$. In SHORT, $\text{SL}_{\text{expanded}} \ge P_{\text{anchor}}$. This ensures outward expansion never moves the stop inside the protected structural level.

### 2.5 Component 5: Maximum Allowable Stop Distance Guard
To prevent chasing an anchor that is too far away or excessive risk exposure:
$$D_{\text{final}} = |P_{\text{entry}} - \text{SL}_{\text{selected}}|$$
$$D_{\text{max\_allowed}} = \text{max\_stop\_distance\_atr} \times \text{ATR}_{M1} \quad (\text{default: } 4.0 \times \text{ATR}_{M1})$$

If $D_{\text{final}} > D_{\text{max\_allowed}}$, the opportunity is rejected with reason `EXCESSIVE_STOP_DISTANCE`.

### 2.6 Component 6: Directional Outward Tick Quantization
All final stops are quantized away from the market to the authoritative instrument tick grid ($0.01 \text{ USD}$):
- **LONG:** $\text{SL} = \lfloor \text{SL}_{\text{ideal}} / \text{tick\_size} \rfloor \times \text{tick\_size}$ (floor outward)
- **SHORT:** $\text{SL} = \lceil \text{SL}_{\text{ideal}} / \text{tick\_size} \rceil \times \text{tick\_size}$ (ceiling outward)

---

## 3. Position Sizing & Net R:R Invariants

### 3.1 Capital & Risk Invariance
When Stop Loss distance $D_{\text{stop}}$ widens from legacy $D_{\text{legacy}}$ to adaptive structural $D_{\text{structural}}$ ($D_{\text{structural}} > D_{\text{legacy}}$):
$$\text{Quantity} \approx \frac{\text{Capital} \times \text{Risk\_Pct}}{D_{\text{stop}} + \text{Slippage} + \text{Fees}}$$
Because $D_{\text{stop}}$ is in the denominator, position size $\text{Quantity}$ decreases proportionally. The total potential dollar loss remains locked to the configured risk budget.

### 3.2 Net R:R Strict Preservation
Net R:R is computed via `calculate_risk_reward` accounting for taker/maker fees and slippage on both entry and exit:
$$\text{Net R:R} = \frac{\text{Net Reward USDT}}{\text{Net Risk USDT}} \ge \text{min\_net\_rr} \quad (\text{policy default: } 1.50 \text{ or } 2.00)$$

If the structural stop widens such that Net R:R drops below $\text{min\_net\_rr}$, **the trade is rejected** with code `NET_RR_TOO_LOW`. Take Profit is never artificially extended.

---

## 4. Policy Configuration & Atomic Updates

System configuration keys managed under `stop_placement_v1.*`:
- `stop_placement_v1.mode`: `LEGACY`, `SHADOW_COMPARE` (default), or `PAPER_STRUCTURAL`.
- `stop_placement_v1.version`: Integer CAS version counter.
- `stop_placement_v1.min_buffer_ticks`: Minimum anchor buffer ticks (default 10).
- `stop_placement_v1.min_protective_ticks`: Minimum entry protective ticks (default 10).
- `stop_placement_v1.atr_period`: SMA True Range period (default 14).
- `stop_placement_v1.atr_m1_multiplier`: Multiplier for M1 ATR buffer (default 0.25).
- `stop_placement_v1.atr_m5_multiplier`: Multiplier for M5 ATR buffer (default 0.10).
- `stop_placement_v1.spread_multiplier`: Multiplier for quote spread buffer (default 1.50).
- `stop_placement_v1.min_distance_atr_m1_mult`: Protective M1 ATR multiplier (default 0.50).
- `stop_placement_v1.min_distance_atr_m5_mult`: Protective M5 ATR multiplier (default 0.20).
- `stop_placement_v1.min_distance_spread_mult`: Protective spread multiplier (default 2.00).
- `stop_placement_v1.max_stop_distance_atr`: Maximum allowable stop distance multiple (default 4.00).
- `stop_placement_v1.max_spread_usd`: Maximum allowable spread before market shock rejection (default 2.00).
- `stop_placement_v1.algorithm_version`: `"V17_7"`.

All policy updates require optimistic concurrency control (`expected_version`). Concurrent conflicting updates fail with `409 Conflict`. Identical updates are treated as true no-ops without incrementing the version.

---

## 5. Execution State Machine & Lifecycle Freezing

1. **Candidate Stage:** Structural anchor and volatility context are evaluated; decision is stored in `cand.stop_placement`.
2. **ARM Stage:** Evaluator verifies candidate SL matches calculator SL (`STOP_PLAN_MISMATCH` check) and executes `validate_applied_stop_decision`. Stop policy metadata, algorithm version, and evidence are frozen into `arm_snap`.
3. **Post-ARM Invariant:** Once ARMED, the order's stop loss is immutable. Later volatility changes cannot widen or adjust the armed stop.
4. **FILL Stage:** Execution coordinator re-verifies Net R:R using actual fill price. Quantity is preserved or downsized, never increased.
5. **Exit Stage:** `shared_exit_evaluator.py` evaluates real-time Bid/Ask quotes against the frozen SL. Stop triggers are never suppressed.
