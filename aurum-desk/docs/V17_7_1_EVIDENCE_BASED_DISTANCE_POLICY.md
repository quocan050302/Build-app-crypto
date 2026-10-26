# AURUM DESK V17.7.1 — EVIDENCE-BASED STOP DISTANCE POLICY
**Role:** Principal Quant Trading Systems Engineer & Risk Management Architect  
**Date:** October 2026  
**Branch:** `feat/v17-7-adaptive-structural-stop-loss`  
**Version:** V17.7.1  

---

## 1. Core Paradigm: Structural Invalidation vs. Volatility Measurements

### 1.1 Fundamental Principle
**ATR is a volatility measurement, not an absolute authority for structural invalidation distance.**

In SMC/ICT trading on XAUUSDT, market structure is defined by swing highs, swing lows, liquidity sweeps, order blocks, and fair value gaps. A legitimate structural invalidation point (such as a major session swing low or Asian liquidity sweep extreme) may be located 5×, 7×, or 8× the 1-minute Average True Range (ATR M1) away from the trigger entry.

Rejecting such a trade solely because:
$$\text{Stop Distance} > 4.0 \times \text{ATR}_{\text{M1}}$$
is conceptually flawed. It forces the system to either:
1. Reject sound, high-probability setups that have clear structural invalidation; or
2. Artificially tighten the stop closer to the entry (inside the structural swing), creating fragile stops that get prematurely swept by microstructure noise.

### 1.2 The V17.7.1 Invariant
A structurally valid Stop Loss must **never** be rejected solely because its distance exceeds an ATR multiple or an arbitrary fixed USD ceiling.

Distance is **not** the primary eligibility criterion.

---

## 2. The 12 Mandatory Stop Loss Eligibility Criteria

In V17.7.1, Stop Loss eligibility depends strictly on satisfying all twelve objective criteria:

1. **Verified Structural Invalidation:** The stop protects a verified, confirmed swing high/low, sweep extreme, or proven leg origin.
2. **Correct Directional Price Geometry:**
   - LONG: $\text{Stop Loss} < \text{Planned Entry} < \text{Take Profit}$
   - SHORT: $\text{Take Profit} < \text{Planned Entry} < \text{Stop Loss}$
3. **Causal Market Evidence:** All anchor data, pivots, and breaks occurred strictly before the decision cutoff ($t_{\text{known}} \le t_{\text{decision}}$). Zero lookahead.
4. **Valid Executable Quote & Spread:** Non-stale quote, finite bid/ask, spread within shock ceiling ($\text{spread} \le \text{max\_spread\_usd}$).
5. **Valid Instrument Metadata:** Respects symbol tick size ($0.01$ USD) and lot step ($0.01$ oz).
6. **Realistic Execution Cost Assumptions:** Incorporates maker/taker fees and adverse execution slippage.
7. **Position Sizing Within Risk Budget:** Position size is dynamically scaled down by `domain_calculator.calculate_risk_reward()` so that dollar risk equals the authorized budget.
8. **Margin & Liquidation Safeguards:** The stop is reached before the estimated liquidation price ($\text{SL} > \text{LP}$ for LONG, $\text{SL} < \text{LP}$ for SHORT).
9. **Independent, Evidence-Based Take Profit:** TP is anchored to real structural liquidity targets (opposing swing, equal highs/lows, unmitigated POI). Never artificially inflated.
10. **Strict Net Risk:Reward Eligibility:** Realized Net R:R after all fees and slippage meets or exceeds policy minimum ($\text{Net R:R} \ge 1.5$ or $2.0$).
11. **Strategy Entry Eligibility:** MSS, Sweep, and FVG criteria of the parent strategy (e.g. CG01, M1 Reversal) must be fully satisfied.
12. **Account & Execution Guards:** Max 3 daily fills, single active position guard, and news blackout filters are satisfied.

If all 12 criteria are met, the setup **must be accepted**, even if its stop distance is 7.5× or 10× ATR M1.

---

## 3. Illustrative Example: Distant but Valid Structural Stop

Consider the following verified setup:
```
Symbol:                     XAUUSDT
Direction:                  LONG
Planned Entry:              2700.00 USD
Protected Structural Low:   2678.00 USD
Protective Buffer:             0.50 USD
Selected Structural SL:     2677.50 USD
Stop Distance:                22.50 USD (2,250 ticks)
ATR M1:                        3.00 USD
Distance / ATR M1:             7.50x ATR (Elevated / Extreme)

Independent Liquidity TP:   2760.00 USD
Gross Reward Distance:        60.00 USD
Gross Risk:Reward:             2.67 : 1
```

### Financial Execution Evaluation:
1. **Risk Budget:** Configured at $50.00 USD (0.05% of $100,000 capital).
2. **Authoritative Calculation:**
   $$\text{Risk Per Unit} \approx 22.50 + 0.02 (\text{fees}) + 0.01 (\text{slippage}) = 22.53 \text{ USD/oz}$$
   $$\text{Quantity} = \frac{50.00}{22.53} \approx 2.219 \xrightarrow{\text{quantize step 0.01}} 2.21 \text{ oz}$$
3. **Net Loss at SL:** $2.21 \times 22.53 \approx 49.79 \text{ USD} \le 50.00 \text{ USD}$ (Risk Budget strictly preserved).
4. **Net Reward at TP:** $2.21 \times 59.97 \approx 132.53 \text{ USD}$.
5. **Net R:R:** $\frac{132.53}{49.79} \approx 2.66 \ge 1.50$ (PASS).
6. **Verdict:** **ACCEPTED (ELIGIBLE)** with diagnostic assessment `DISTANCE_EXTREME`.

If capital were only $10.00 USD, required quantity would be $10 / 22.53 \approx 0.44$ oz $\ge 0.01$ oz (PASS). If capital were $0.10 USD, quantity would be $< 0.01$ oz, rejecting with `MIN_QTY_EXCEEDS_BUDGET`. The financial engine makes the decision, never an arbitrary ATR limit.

---

## 4. Non-Blocking Diagnostic Distance Classifications

In `StopPlacementDecision`, the field `distance_assessment` provides full explainability for quant research without blocking valid trades:

| Assessment Code | Condition | Operational Meaning |
|---|---|---|
| `DISTANCE_NORMAL` | $\text{Distance} \le 2.5 \times \text{ATR}_{\text{M1}}$ | Standard microstructure stop distance. |
| `DISTANCE_ELEVATED` | $2.5 \times \text{ATR}_{\text{M1}} < \text{Distance} \le \text{ref\_atr\_mult}$ | Wider than normal swing; normal sizing downscale applied. |
| `DISTANCE_EXTREME` | $\text{Distance} > \text{ref\_atr\_mult}$ (e.g. $> 4.0\times$) | Macro structural anchor; heavy sizing downscale applied. Eligible if Net R:R and min qty pass. |
| `DISTANCE_ATR_UNAVAILABLE` | $\text{ATR}_{\text{M1}}$ missing or non-positive | Missing volatility context recorded for audit. |

---

## 5. Absolute Prohibitions

1. **NO Post-ARM Stop Widening:** Once an order is ARMED, its stop loss is immutable.
2. **NO Take Profit Manipulation:** TP must never be moved farther away just to satisfy Net R:R against a distant SL.
3. **NO Allowed Risk Expansion:** Never increase the permitted dollar risk to accommodate a wider stop.
4. **NO Invented ATR:** Missing ATR evidence must never be replaced with hardcoded values.
