# AURUM DESK V17.7.2 — ATR CAUSALITY AND STOP LOSS ELIGIBILITY
**Release:** V17.7.2  
**Focus:** Strict Fail-Closed Volatility Gate, Causal Timestamps, Anchor Validation, Non-Inverted Quote Authority  
**Status:** VERIFIED_FROM_TEST  

---

## 1. Required Volatility Evidence & Strict Fail-Closed Architecture

### 1.1 The P0-01 Defect in V17.7.1
In V17.7.1, `plan_structural_stop()` classified missing M1 ATR as `DISTANCE_ATR_UNAVAILABLE` under `distance_assessment`, but proceeded to compute a fallback buffer based solely on tick floor, returning `status = StopStatus.VALID.value` with an executable `selected_sl`. This allowed candidates to enter the execution pipeline without verified volatility context.

### 1.2 The V17.7.2 Fail-Closed Barrier
In V17.7.2, Step 8 of `plan_structural_stop()` strictly enforces the following invariant:
```python
if mode == StopPlacementMode.PAPER_STRUCTURAL.value and (eff_m1 is None or eff_m1 <= 0.0 or not math.isfinite(eff_m1)):
    return StopPlacementDecision(
        status=StopStatus.WAITING_VOLATILITY_EVIDENCE.value,
        selected_sl=None,
        applied_variant="NONE",
        buffer=None,
        reasons=("WAITING_VOLATILITY_EVIDENCE", "REQUIRED_M1_ATR_UNAVAILABLE"),
        distance_assessment="DISTANCE_ATR_UNAVAILABLE",
        as_of_ms=as_of_ms
    )
```

### 1.3 Volatility Conditions Tested and Blocked (CE-01 to CE-05)
- `CE-01`: Missing M1 ATR object and raw value -> Blocks arming with `status = WAITING_VOLATILITY_EVIDENCE`, `selected_sl = None`.
- `CE-02`: Non-finite ATR values (`NaN`, `+Inf`, `-Inf`, `0.0`, `-1.5`) -> Blocks arming fail-closed.
- `CE-03`: Insufficient closed candles (< 14 closed bars) -> `compute_stop_atr` returns `status = UNAVAILABLE`, blocking arming.
- `CE-04`: Unclosed / in-progress candles -> Strictly excluded from ATR computation; cutoff enforces `open_time + tf_duration <= as_of_ms`.
- `CE-05`: Optional M5 ATR -> When M1 ATR is available and valid, missing M5 ATR does not block execution (handled per explicit policy).

---

## 2. Structural Anchor Authority & Causal Ordering

### 2.1 Causal Ordering Invariant (CE-06)
Every structural anchor must satisfy strict temporal ordering relative to the decision cutoff `as_of_ms`:
$$\text{occurred\_at\_ms} \le \text{known\_at\_ms} \le \text{as\_of\_ms}$$

- If $\text{occurred\_at\_ms} > \text{known\_at\_ms}$, the anchor claims to have been known before it occurred (chronological paradox).
- If $\text{known\_at\_ms} > \text{as\_of\_ms}$, the anchor represents lookahead bias (known only after decision time).

Both cases deterministically return `StopStatus.INVALID_DATA.value` with reason `CAUSAL_ORDERING_VIOLATION`.

### 2.2 Episode Binding & Invalidation Protection (CE-07 & CE-08)
- `CE-07`: Anchors belonging to an unrelated structural break or foreign origin pivot are rejected with `MISSING_DATA` or `INVALID_DATA`.
- `CE-08`: Any anchor whose price level has been breached by subsequent closed candle wicks or bodies prior to `as_of_ms` is rejected with `ANCHOR_ALREADY_INVALIDATED`.

---

## 3. Canonical Bid/Ask Quote & Spread Integrity

### 3.1 Elimination of Inverted Quote Masking (CE-09)
In legacy code, `spread = max(0.0, ask - bid)` was applied. If an exchange feed reported an inverted quote (e.g., `bid = 2701.0`, `ask = 2700.0`), the negative spread was clamped to `0.0`, disguising a broken market state as a tight spread.

In V17.7.2:
1. `compute_stop_buffer` raises `ValueError("INVERTED_QUOTE")` if `ask < bid`.
2. `plan_structural_stop` checks `spread < 0.0` or `ask < bid` and returns `StopStatus.INVALID_DATA.value` with reasons `("INVALID_SPREAD", "INVERTED_QUOTE")`.
3. `adapt_cg01_quote` preserves `ask - bid` and marks `is_valid = False` whenever `bid <= 0`, `ask <= 0`, `ask < bid`, or values are non-finite.

### 3.2 Stale Quote Rejection (CE-10)
`evaluate_cg01_candidate()` inspects quote freshness prior to strategy evaluation. Stale quotes (`is_stale = True` or timestamp older than configured limit) immediately exit with `STALE_QUOTE` and `QUOTE_STALE`, preventing any trade setup.

---

## 4. Why Distant SL (> 4.0× ATR) Remains Permitted

Distant Stop Losses are not inherently invalid. In high-volatility structural environments, a genuine swing low or protected leg high may legitimately sit 5×, 7×, or 10× M1 ATR away from entry.

Under V17.7.2:
- An arbitrary distance cap (e.g. `stop_distance > 4.0 * atr_m1`) is **not** enforced.
- When an SL is distant, quantity is automatically scaled down by `domain_calculator.calculate_risk_reward()` so that total risk does not exceed the risk budget.
- The trade is accepted if and only if:
  1. The anchor is causally valid, confirmed, and unbreached.
  2. M1 ATR is verified and available.
  3. Minimum quantity and minimum notional constraints pass.
  4. Net R:R meets the required threshold with an independent target.
  5. Liquidation price remains safely beyond the Stop Loss level.
