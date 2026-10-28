# AURUM DESK V17.8 — AUTHORITATIVE RISK CALCULATOR & PAPER LIFECYCLE INVARIANTS

## 1. Single Authoritative Financial Source
All financial risk, margin sizing, fees, and Net R:R calculations throughout Aurum Desk are computed exclusively by:
`backend/domain_calculator.py:calculate_risk_reward()`

No module, service, or test is permitted to implement custom or alternate fee/slippage equations.

---

## 2. Risk Budget Invariant
The standard scalp risk configuration is:
- **Capital:** $1,000.00 USDT
- **Risk Percentage:** `0.25%` -> **Risk Budget:** `$2.50 USDT`
- **Leverage:** `20x` Isolated Margin

When stop distance expands (e.g. from $0.59 under legacy FVG edge to $3.50 under Breaker distal invalidation), the position quantity **must strictly downscale**:
$$Quantity = \frac{Budget_{net}}{Distance_{SL}}$$
- At $d_{SL} = 0.50$, $Q \approx 0.65$ oz
- At $d_{SL} = 3.50$, $Q \approx 0.15$ oz
- At $d_{SL} = 10.00$, $Q \approx 0.04$ oz

In all cases, total loss upon hitting SL equals $2.50 USDT $\pm$ tick quantization tolerance.

---

## 3. Paper Order Lifecycle Invariants

### 3.1 State Transitions
```text
(Candidate) -> ARMED -> PAPER_OPEN -> CLOSED
```

### 3.2 Immutability Guarantees
1. **Stop Loss Freeze:** Once an order transitions to `ARMED`, `stop_loss` is permanently frozen in `arm_decision_snapshot`. Under no circumstances may an ARMED or OPEN stop loss be widened.
2. **Quantity Ceiling:** `filled_quantity` cannot exceed `authorized_quantity`.
3. **Execution Mode Isolation:** Only `PAPER` accounts are authorized. Live exchange endpoints are physically blocked.
4. **Isolated Exit Evaluator:** `shared_exit_evaluator.py` evaluates exits on closed candle or real-time tick prices with conservative gap-handling.
