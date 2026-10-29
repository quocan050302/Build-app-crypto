# AURUM DESK V17.8.0.4 — OUT-OF_SAMPLE ADEQUACY & ZERO-TRADE SEMANTICS

## 1. Executive Summary
- **Final Code SHA:** `c356d6551e13cd6af61cea5c3ab1f4aae1548af2`
- **Backtest Run ID:** `v17_8_0_4_bt_1793248253`
- **Verification Gate:** `G83_OOS_ZERO_TRADE_INSUFFICIENT_SAMPLE_HANDLING` (`PASS`)
- **Out-of-Sample Verdict:** `OUT_OF_SAMPLE_INCONCLUSIVE`
- **Split Protocol:** `CHRONOLOGICAL_60_20_20_ZERO_LOOKAHEAD`
- **Minimum Required OOS Trades for Conclusive Verdict:** `10`

## 2. Primary Chronological 60% Train / 20% Validation / 20% Out-of-Sample Split
| Split | Opportunities | Variant | Simulated Trades | Performance Status | Win Rate | Net Expectancy (R) | Net PnL (USDT) |
|---|---:|---|---:|---|---:|---:|---:|
| **Train (60%)** | `2303` | A | `20` | `SUFFICIENT_SAMPLE` | `0.05` | `-0.9122` | `-44.2037` |
| **Train (60%)** | `2303` | C | `11` | `SUFFICIENT_SAMPLE` | `0.3636` | `-0.3646` | `-9.5269` |
| **Validation (20%)** | `768` | A | `9` | `LOW_SAMPLE_CAUTION` | `0.3333` | `-0.3743` | `-7.9626` |
| **Validation (20%)** | `768` | C | `6` | `LOW_SAMPLE_CAUTION` | `0.1667` | `-0.8147` | `-11.8462` |
| **Out-of-Sample (20%)** | `768` | A | `1` | `LOW_SAMPLE_CAUTION` | `0.0` | `-0.2146` | `-0.5278` |
| **Out-of-Sample (20%)** | `768` | C | `0` | `INSUFFICIENT_SAMPLE` | `None` | `None` | `0` |
