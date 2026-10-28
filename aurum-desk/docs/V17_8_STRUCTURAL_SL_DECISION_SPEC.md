# AURUM DESK V17.8 — STRUCTURAL STOP LOSS DECISION SPECIFICATION

## 1. Principles of Thesis-Bound Invalidation
In Aurum Desk V17.8, a Stop Loss is defined not by arbitrary monetary amounts or distance cutoffs (such as 4x ATR), but by the structural market price that **invalidates the underlying thesis** of the setup.

### 1.1 Invalidation Authority Table

| Model ID | Setup Type | Primary Invalidation Anchor | Fallback Invalidation Anchor |
|---|---|---|---|
| `ICT_UNICORN` / `SMC_M1_UNICORN` | Breaker + FVG Retest | Breaker Block Distal Boundary (`BREAKER_DISTAL_BOUNDARY`) | Protected origin of break leg |
| `SMC_M1_FVG_CONTINUATION` | FVG Retest Continuation | Protected origin of break leg (`PROTECTED_LEG_HIGH/LOW`) | Displacement origin swing preceding FVG (`DISPLACEMENT_ORIGIN`) |
| `SMC_M1_CONTINUATION` | Swing Pullback Continuation | Protected BOS/MSS swing extreme | Local protected origin |
| `SMC_M1_REVERSAL` | Liquidity Sweep Reversal | Sweep Extreme belonging to episode | Nearest swing extreme |
| `SMC_M1_SWEEP_RECLAIM` | Sweep & Level Reclaim | Sweep Extreme of reclaimed level | Episode anchor |
| `CG01_SWEEP_MSS_FVG` | CG01 Confluence Setup | CG01 Sweep Extreme | MSS swing origin |

---

## 2. Invalidation Authority Ranking
When multiple anchors are present, `StopPlacementService` applies `THESIS_BOUND_INVALIDATION` ranking:
1. **Unicorn Confluence:** If `unicorn_evidence` is valid, `BREAKER_DISTAL_BOUNDARY` holds absolute priority.
2. **Setup-Bound Origin:** The protected extreme of the displacement leg producing the setup.
3. **Displacement Origin Fallback:** If the break leg is dated (> 30 bars) or already breached, the highest swing high (for SHORT) or lowest swing low (for LONG) occurring between the displacement start and FVG formation is selected.
4. **Fail-Closed Guarantee:** If no causal structural anchor can be identified, the service returns `StopAnchorResult(status="WAITING_STRUCTURE")`. In `PAPER_STRUCTURAL` mode, fallback to legacy edge stop is strictly forbidden.

---

## 3. Dynamic Volatility Buffer Integration
The raw anchor price is padded outward by the dynamic volatility buffer:
```text
buffer_price = max(
    min_buffer_ticks * tick_size,
    atr_m1_multiplier * raw_atr_m1,
    atr_m5_multiplier * raw_atr_m5,
    spread_multiplier * spread
)
```

For SHORT:
```text
unquantized_sl = anchor_price + buffer_price
selected_sl = ceil(unquantized_sl / tick_size) * tick_size
```

For LONG:
```text
unquantized_sl = anchor_price - buffer_price
selected_sl = floor(unquantized_sl / tick_size) * tick_size
```

---

## 4. Distant Stop Loss Assessment
A stop loss whose distance exceeds `4 * ATR_M1` is not rejected if the structural anchor is valid. Instead, it is assigned a non-blocking diagnostic tag:
- Distance <= 2.0x ATR: `DISTANCE_NORMAL`
- 2.0x < Distance <= 4.0x ATR: `DISTANCE_ELEVATED`
- Distance > 4.0x ATR: `DISTANCE_EXTREME`

The position size is downscaled by `domain_calculator` to preserve the exact risk budget.
