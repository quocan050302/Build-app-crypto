# AURUM DESK V17.7.3 — STRUCTURAL SL FORMULA VERIFICATION
**Mathematical Proof, Multi-Timeframe Buffer & Distant Stop Financial Integrity**  
**Repository:** `quocan050302/Build-app-crypto`  
**Branch:** `feat/v17-7-adaptive-structural-stop-loss`  
**Status:** `VERIFIED_FROM_UNIT_TEST`

---

## 1. Stop Loss Philosophy & Governance Invariants

### 1.1 Distance Invariant: No Arbitrary Distance Caps
In SMC/ICT trading, high-timeframe structural sweeps or extended multi-leg expansions can place the invalidation anchor substantially farther than typical volatility boundaries:
- A structural stop may be $4\times$, $6\times$, $8\times$, or $10\times$ ATR away from Entry.
- **Rule:** Stop Loss distance alone is **never** a valid reason to reject an evidence-backed structural stop.
- The system determines whether the stop is **structurally valid** (anchored to a confirmed, causal swing high/low) and **financially executable** (satisfies risk budget, sizing steps, minimum Net R:R, margin, and liquidation limits).

### 1.2 Multi-Source Protective Buffer Formula
The protective buffer added beyond the structural anchor is calculated as:
$$\text{Buffer} = \max \Big( \text{min\_tick\_buffer}, \; \mu_{M1} \times ATR_{M1}^{\text{raw}}, \; \mu_{M5} \times ATR_{M5}^{\text{raw}}, \; \mu_{\text{spread}} \times \text{Spread} \Big)$$
where:
- $\text{min\_tick\_buffer} = 2.0 \times \text{tick\_size}$
- $\mu_{M1} = \text{policy.atr\_m1\_multiplier}$ (default $0.25$)
- $\mu_{M5} = \text{policy.atr\_m5\_multiplier}$ (default $0.15$)
- $\mu_{\text{spread}} = \text{policy.spread\_multiplier}$ (default $1.50$)
- $ATR_{M1}^{\text{raw}}, ATR_{M5}^{\text{raw}}$: Authoritative unweighted ATR metrics.

### 1.3 Stop Loss Placement Rules
- **For LONG setups:**
  $$\text{Selected SL} = \text{Anchor Low} - \text{Buffer}$$
  Must satisfy: $\text{Selected SL} < \text{Anchor Low} \le \text{Entry} < \text{TP}$
- **For SHORT setups:**
  $$\text{Selected SL} = \text{Anchor High} + \text{Buffer}$$
  Must satisfy: $\text{TP} < \text{Entry} \le \text{Anchor High} < \text{Selected SL}$

---

## 2. Complete Concrete Examples (Deterministic Walkthroughs)

### 2.1 Concrete Example 1: Distant LONG Structural Stop (7.50x ATR)
A high-volatility expansion on XAUUSDT creates an entry at 2700.00 with a confirmed structural sweep low at 2678.00:

| Parameter | Value | Calculation / Derivation |
|---|---|---|
| **Instrument** | XAUUSDT | Gold spot/perp contract |
| **Direction** | `LONG` | Bullish continuation |
| **Planned Entry** | `2700.00 USD` | Limit / Market entry price |
| **Confirmed Anchor** | `2678.00 USD` | Protected structural sweep low |
| **Raw ATR M1** | `3.00 USD` | 14-period SMA of TR from closed 1M candles |
| **Raw ATR M5** | `5.00 USD` | 14-period SMA of TR from closed 5M candles |
| **Validated Spread** | `0.20 USD` | Current ask - bid |
| **Tick Size** | `0.01 USD` | Market minimum price step |
| **Buffer Component: M1** | `0.75 USD` | $0.25 \times 3.00$ |
| **Buffer Component: M5** | `0.75 USD` | $0.15 \times 5.00$ |
| **Buffer Component: Spread** | `0.30 USD` | $1.50 \times 0.20$ |
| **Buffer Component: Min Tick** | `0.02 USD` | $2.0 \times 0.01$ |
| **Protective Buffer** | `0.75 USD` | $\max(0.02, 0.75, 0.75, 0.30) = 0.75$ |
| **Selected Stop Loss** | `2677.25 USD` | $2678.00 - 0.75 = 2677.25$ |
| **SL Distance** | `22.75 USD` | $|2700.00 - 2677.25| = 22.75$ |
| **Distance / Raw ATR M1** | **7.58x** | $22.75 / 3.00 = 7.5833\times$ (> 4.0x ATR) |
| **Independent TP** | `2760.00 USD` | HTF liquidity draw ($+60.00$ USD distance) |
| **Risk Budget** | `25.00 USD` | Configured risk allocation |
| **Calculated Quantity** | `1.09 oz` | $\lfloor 25.00 / 22.75 \rfloor$ stepped to $0.01$ |
| **Gross Reward** | `65.40 USD` | $1.09 \times (2760.00 - 2700.00)$ |
| **Fees & Slippage** | `3.10 USD` | Roundtrip fees + slippage |
| **Net Reward** | `62.30 USD` | $65.40 - 3.10$ |
| **Net R:R Ratio** | **2.49** | $62.30 / 25.00 = 2.492 \ge 1.50$ (Threshold) |
| **Final Eligibility** | **ELIGIBLE** | Structural anchor valid, financial invariants pass |

---

### 2.2 Concrete Example 2: Distant SHORT Structural Stop (8.17x ATR)
A bearish sweep of Asian highs on XAUUSDT creates an entry at 2650.00 with a confirmed structural sweep high at 2674.00:

| Parameter | Value | Calculation / Derivation |
|---|---|---|
| **Instrument** | XAUUSDT | Gold spot/perp contract |
| **Direction** | `SHORT` | Bearish reversal |
| **Planned Entry** | `2650.00 USD` | Limit / Market entry price |
| **Confirmed Anchor** | `2674.00 USD` | Protected structural sweep high |
| **Raw ATR M1** | `3.00 USD` | 14-period SMA of TR from closed 1M candles |
| **Raw ATR M5** | `5.00 USD` | 14-period SMA of TR from closed 5M candles |
| **Validated Spread** | `0.20 USD` | Current ask - bid |
| **Tick Size** | `0.01 USD` | Market minimum price step |
| **Buffer Component: M1** | `0.75 USD` | $0.25 \times 3.00$ |
| **Buffer Component: M5** | `0.75 USD` | $0.15 \times 5.00$ |
| **Buffer Component: Spread** | `0.30 USD` | $1.50 \times 0.20$ |
| **Buffer Component: Min Tick** | `0.02 USD` | $2.0 \times 0.01$ |
| **Protective Buffer** | `0.75 USD` | $\max(0.02, 0.75, 0.75, 0.30) = 0.75$ |
| **Selected Stop Loss** | `2674.75 USD` | $2674.00 + 0.75 = 2674.75$ |
| **SL Distance** | `24.75 USD` | $|2674.75 - 2650.00| = 24.75$ |
| **Distance / Raw ATR M1** | **8.25x** | $24.75 / 3.00 = 8.25\times$ (> 4.0x ATR) |
| **Independent TP** | `2580.00 USD` | HTF sell-side liquidity pool ($-70.00$ USD distance) |
| **Risk Budget** | `25.00 USD` | Configured risk allocation |
| **Calculated Quantity** | `1.01 oz` | $\lfloor 25.00 / 24.75 \rfloor$ stepped to $0.01$ |
| **Gross Reward** | `70.70 USD` | $1.01 \times (2650.00 - 2580.00)$ |
| **Fees & Slippage** | `3.15 USD` | Roundtrip fees + slippage |
| **Net Reward** | `67.55 USD` | $70.70 - 3.15$ |
| **Net R:R Ratio** | **2.70** | $67.55 / 25.00 = 2.702 \ge 1.50$ (Threshold) |
| **Final Eligibility** | **ELIGIBLE** | Structural anchor valid, financial invariants pass |

---

## 3. Financial Invariant Protections

### 3.1 Quantity Reduction on Wider Stop
Under a fixed risk budget $R = 25.00$ USD:
- Normal Stop (Distance = 5.00 USD):
  $$\text{Quantity} = \frac{25.00}{5.00} = 5.00 \text{ oz}$$
- Distant Stop (Distance = 25.00 USD):
  $$\text{Quantity} = \frac{25.00}{25.00} = 1.00 \text{ oz}$$
The sizing engine dynamically contracts position size so that the maximum potential loss never exceeds the authorized risk budget.

### 3.2 Prohibited Manipulations
1. **Never Inflate TP**: The target price is strictly derived from HTF structural liquidity. The system is prohibited from widening TP to artificially meet Net R:R requirements.
2. **Never Increase Risk Budget**: The risk budget is immutable per trading policy. Sizing cannot increase risk to accommodate a wide stop.
3. **No Post-ARM Widening**: Once an order is ARMED, its Stop Loss price is strictly frozen.
4. **No Fill-Time Quantity Growth**: The quantity filled at broker execution cannot exceed the authorized quantity computed at ARM time.
