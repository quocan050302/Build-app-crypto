# AURUM DESK V17.8.0.3 — REAL HISTORICAL BACKTEST PROTOCOL

**Release:** `V17.8.0.3`  
**Module:** `backend/services/v17_8_backtest_runner.py`

---

## 1. Causal Replay Methodology

1. **Chronological Bar-by-Bar Stepping**:
   - Steps through verified closed M1 bars (`16,645` bars) in strict chronological order (`decision_cutoff_ms = bar.close_time`).
   - At each step `t`, only M1, M5, and M15 candles with `close_time <= decision_cutoff_ms` are visible to the evaluator (`0` future candle lookahead).
2. **Executable Spread & Quote Modeling**:
   - Models realistic Bid/Ask spread around bar close (`base_spread = 0.20` USD in baseline; stressed to `0.30` USD and `0.40` USD in stress scenarios).
   - LONG entries execute at `Ask` (`close + spread/2` + slippage); LONG stops/targets evaluate against `Bid`.
   - SHORT entries execute at `Bid` (`close - spread/2` - slippage); SHORT stops/targets evaluate against `Ask`.
3. **Intrabar Ambiguity Rule**:
   - When a subsequent M1 bar's range spans both `stop_loss` and `take_profit`, the replay engine applies the conservative **SL-first** rule (`INTRABAR_BOTH_HIT_CONSERVATIVE_SL_FIRST`) and increments `intrabar_ambiguous_bars`.
   - Sub-minute `30s` stop-out rate is explicitly disclosed as `"UNAVAILABLE_FROM_M1_OHLC"` because 1-minute OHLC bars do not contain 30-second tick timestamps.

---

## 2. Matched Variants A / B / C / D Protocol

Every detected setup episode (`5,440` opportunities) is evaluated across all four stop-placement variants under identical entry reference, target selection, and `$2.50` risk budget:
- **Variant A (`LEGACY_FIXED_TICKS`)**: Tight local wick + fixed tick buffer (`2` ticks).
- **Variant B (`STRUCTURAL_TICK_FLOOR`)**: V17.7 structural invalidation anchor + minimum tick floor (`max(2 ticks, spread + 1 tick)`).
- **Variant C (`STRUCTURAL_DYNAMIC_BUFFER`)**: V17.8 Unicorn/structural invalidation anchor (`BREAKER_DISTAL_BOUNDARY` when valid Unicorn confluence exists, else structural swing/displacement origin) + dynamic volatility/spread buffer (`max(2 ticks, 0.25 * ATR_M1, 0.10 * ATR_M5, 1.5 * spread)`).
- **Variant D (`EXPERIMENTAL_FIXED_MULTIPLIER`)**: Structural anchor + empirical P90 noise buffer calibrated strictly from In-Sample excursion distribution (with `0` Out-of-Sample leakage).

---

## 3. Out-of-Sample & Stress Protocol

- **Chronological Split**: First `70%` of opportunities (`3,808` setups) = **In-Sample (IS)**; final `30%` (`1,632` setups) = **Out-of-Sample (OOS)**.
- **Stress Scenarios**:
  1. `BASELINE` (`1.0x` spread, `0` extra slippage, `0.06%` taker fee)
  2. `SPREAD_1_5X` (`1.5x` spread)
  3. `SPREAD_2_0X` (`2.0x` spread)
  4. `SLIPPAGE_PLUS_1_TICK` (`+1` tick adverse slippage on entry & exit)
  5. `SLIPPAGE_PLUS_2_TICKS` (`+2` ticks adverse slippage on entry & exit)
  6. `CONSERVATIVE_TAKER_FEES` (`1.5x` fee rate)

