# AURUM DESK V17.8 — LIQUIDITY TAKE PROFIT INTELLIGENCE SPECIFICATION

## 1. Absolute Take Profit Independence Invariant
**Core Invariant:** Take Profit is determined solely by independent market liquidity structure. It is **NEVER** calculated by multiplying Stop Loss distance by a desired R:R ratio (e.g. `TP = Entry + (SL_distance * 2.0)`).

If an authoritative structural SL results in a Net R:R lower than the required minimum (`min_net_rr = 1.5`), the system **must emit `NO_TRADE_INSUFFICIENT_NET_RR`** and fail closed. It is strictly forbidden to move TP farther away to fabricate an acceptable Net R:R.

---

## 2. Liquidity Target Classification & Ranking

Candidate Take Profit targets are evaluated from `smc_liquidity_service.py` across multiple timeframes:
1. **INTERNAL_LIQUIDITY:** Opposing Fair Value Gaps or internal unmitigated imbalance zones.
2. **EXTERNAL_LIQUIDITY:** Equal Highs (EQH), Equal Lows (EQL), or unconfirmed opposing swing points.
3. **SESSION_EXTREMES:** Previous Session High (PSH), Previous Session Low (PSL), Asia Range High/Low.
4. **HTF_OBJECTIVES:** Daily / 4H swing liquidity pools.

### Selection Criteria
- **Directional Geometry:** For SHORT, target price must be strictly less than planned entry (`TP < Entry`). For LONG, target price must be strictly greater than planned entry (`TP > Entry`).
- **Unconsumed Status:** The target level must not have been swept or mitigated prior to decision time `as_of_ms`.
- **Causal Formation:** The liquidity pool must have formed before the decision cutoff.

---

## 3. Multi-Target Framework (Research Mode)
The V17.8 comparison engine supports research tracking across 3 target horizons:
- `TP1_INTERNAL`: Conservative internal liquidity objective (higher win rate, shorter hold time).
- `TP2_EXTERNAL`: Canonical structural objective (default executable target).
- `TP3_HTF`: Extended trend objective.

In production executable mode, exactly one canonical target is bound to the order at ARM and frozen.
