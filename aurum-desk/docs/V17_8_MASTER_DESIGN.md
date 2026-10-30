# AURUM DESK V17.8 — MASTER ARCHITECTURE & SYSTEM DESIGN SPECIFICATION
## ICT UNICORN-ASSISTED STRUCTURAL SL/TP & MARKET NOISE INTELLIGENCE

**Release Target:** V17.8.0  
**Repository:** `quocan050302/Build-app-crypto`  
**Base Branch:** `master` (`de5e035fc14ef9f155ecd1421b90f38a11a861e9`)  
**Feature Branch:** `feat/v17-8-unicorn-structural-sl-tp-intelligence`  
**Execution Environment:** PAPER-Only, Isolated Test DBs, Zero Live Dispatch  

---

## 1. Executive Summary & Problem Formulation

### 1.1 The Microstructure Stop-Out Dilemma
In previous versions (V17.7 and earlier), M1 FVG continuation trades were protected using either:
- **Variant A (Legacy Edge Stop):** Placed exactly at `FVG Edge + 5 ticks` ($0.05 on XAUUSDT).
- **Variant B (V17.7 Protected Origin Stop):** Bound to the swing origin producing the market break. However, if the break leg was dated or breached, the system failed closed back to Variant A.

In real market conditions (such as the audited historical trade on Oct 27, 2026), placing a stop loss only $0.59 away from planned entry while M1 ATR was $0.98 and M5 ATR was $3.38 resulted in immediate stop-outs (~9 seconds after FILL) due to normal Bid/Ask spread and microstructure noise, despite the macro bearish thesis remaining intact and price subsequently plummeting towards the profit target.

### 1.2 The V17.8 Solution
Aurum Desk V17.8 delivers a mathematically rigorous, structurally sound trading system:
1. **ICT Unicorn Evidence Engine (`unicorn_evidence_service.py`):** Identifies confluence between a verified ICT Breaker Block and Fair Value Gap (FVG) with causal formation order and geometric overlap.
2. **Unified Structural Stop Loss Intelligence:** Stop loss is anchored to the true market structure that invalidates the trade thesis (`BREAKER_DISTAL_BOUNDARY` for Unicorns, displacement origin for FVG continuation), with dynamic volatility buffer sizing.
3. **Entry & Noise Protection Guards (`entry_guard_service.py`):** Pre-ARM and pre-FILL distance checks ensure executable quotes (Ask for SHORT, Bid for LONG) never penetrate or closely threaten the stop before entry execution.
4. **Authoritative Financial Invariant:** `domain_calculator.calculate_risk_reward()` remains the sole authority. When SL distance widens, position quantity downscales proportionally, conserving the exact risk budget ($2.50 USDT @ 20x). Trades with insufficient Net R:R (< 1.5) fail closed rather than forcing artificially tight stops.

---

## 2. Core Architecture Pipeline

```text
               +-------------------------------------------------+
               |        1M / 5M / 15M Market Data Feed           |
               +-------------------------------------------------+
                                        |
                                        v
               +-------------------------------------------------+
               |       SMC & ICT Pattern Detection Engine        |
               | (Swings, Sweeps, MSS/BOS, FVGs, Breaker Blocks) |
               +-------------------------------------------------+
                                        |
                                        v
               +-------------------------------------------------+
               |          Unicorn Evidence Engine (P1)           |
               |  - Breaker / FVG Confluence & Overlap Check     |
               |  - Causal Episode Binding & Retest Detection    |
               +-------------------------------------------------+
                                        |
                                        v
               +-------------------------------------------------+
               |       Unified Structural SL/TP Resolver (P2/P3) |
               |  - Model-Specific Invalidation Authority        |
               |  - Dynamic ATR / Spread Buffer Computation      |
               |  - Independent Liquidity Target Selection       |
               +-------------------------------------------------+
                                        |
                                        v
               +-------------------------------------------------+
               |   Authoritative Risk Calculator (domain_calc)   |
               |  - Risk Budget Conservation ($2.50 USDT @ 20x)  |
               |  - Proportional Quantity Downscaling            |
               |  - Net R:R & Liquidation Clearance              |
               +-------------------------------------------------+
                                        |
                                        v
               +-------------------------------------------------+
               |        Entry Guard & Noise Protection (P4/P5)   |
               |  - Executable Quote Distance Guard (Ask/Bid)    |
               |  - Unicorn Invalidation Guard                   |
               +-------------------------------------------------+
                                        |
                                        v
               +-------------------------------------------------+
               |            ARM Decision & Persistence           |
               |  - Freeze Selected SL/TP & Sizing Invariants    |
               +-------------------------------------------------+
                                        |
                                        v
               +-------------------------------------------------+
               |       FILL Guards -> PAPER OPEN Execution       |
               +-------------------------------------------------+
                                        |
                                        v
               +-------------------------------------------------+
               |       Shared Exit Evaluator -> CLOSED Order     |
               +-------------------------------------------------+
```

---

## 3. Mathematical Foundations

### 3.1 Dynamic Volatility Buffer
```text
buffer = max(
    min_buffer_ticks * tick_size,
    atr_m1_multiplier * raw_atr_m1,
    atr_m5_multiplier * raw_atr_m5,
    spread_multiplier * spread
)
```
Default parameters:
- `min_buffer_ticks`: 5 ($0.05 on XAUUSDT)
- `atr_m1_multiplier`: 0.25
- `spread_multiplier`: 1.50

### 3.2 Protective Distance & Executable Stop Placement
For SHORT:
```text
structural_stop = breaker_distal_high + buffer
minimum_stop = entry_reference + min_protective_distance
selected_sl = quantize_stop_outward(max(structural_stop, minimum_stop), tick_size=0.01)
```

For LONG:
```text
structural_stop = breaker_distal_low - buffer
minimum_stop = entry_reference - min_protective_distance
selected_sl = quantize_stop_outward(min(structural_stop, minimum_stop), tick_size=0.01)
```

### 3.3 Position Sizing & Risk Conservation
Given capital $C$, risk percentage $r = 0.25\%$, leverage $L = 20$, and stop distance $d_{SL} = |P_{entry} - P_{SL}|$:
```text
Budget_{risk} = C * (r / 100) = 1000 * 0.0025 = 2.50 USDT
Quantity Q = floor_step( (Budget_{risk} - EstimatedFees) / d_{SL}, step=0.01 )
```
As $d_{SL}$ increases from $0.59 to $3.50, $Q$ downscales from $0.65 to $0.15, guaranteeing that total capital at risk remains locked at $2.50 USDT.

---

## 4. Invariant Matrix

| ID | Invariant Category | Rule / Behavior | Fail-Closed Handling |
|---|---|---|---|
| INV-01 | Trading Safety | PAPER trading only; zero live exchange API calls. | Reject live dispatch; terminate process if spy tripped. |
| INV-02 | Financial Safety | Authoritative `domain_calculator.py` controls all sizing. | If Net R:R < 1.5 or margin fails, emit `NO_TRADE`. |
| INV-03 | Stop Modification | Stop loss is frozen at ARM. Never widen an ARMED/OPEN stop. | Mutating stop raises invariant error. |
| INV-04 | Fill Sizing | Fill quantity can never exceed authorized ARM quantity. | Excess fill rejected immediately. |
| INV-05 | Structural Anchor | Distal boundary must belong to verified thesis episode. | Missing anchor -> WAITING_STRUCTURE. |
| INV-06 | Market Noise | Remaining stop distance to executable quote must exceed cushion. | If quote penetrates or is too close, block entry. |
