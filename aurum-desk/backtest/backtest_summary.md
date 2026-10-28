# AURUM DESK V17.8.0.3 — Real Historical Backtest Summary

- **Run ID**: `v17_8_0_3_bt_1793203152`
- **Tested Source SHA**: `3428ed3cef23c7cfad6a28abd78256682ceb3ece`
- **Dataset SHA-256**: `e2c972875e4517c88873c1f6f533db5844ca66f3c2254cd2e720cb028f3dcfa7`
- **Backtest Verdict**: `REAL_HISTORICAL_BACKTEST_COMPLETED`
- **Data Quality Status**: `VERIFIED_HISTORICAL_CANDLES_WITH_DISCLOSED_GAPS`
- **Trading Authorization**: `PAPER_AUTO_TRADING_NOT_AUTHORIZED_BY_V17_8_0_3` / `LIVE_TRADING_NOT_AUTHORIZED`

## 1. Historical Dataset Coverage & Limitations

- **Symbol**: `XAUUSDT` (`USDT-FUTURES`)
- **Requested Window**: `1785420000000` to `1793196000000` (90.0 calendar days)
- **Actual Verified M1 Window**: `1791899220000` to `1793195940000` (`16645` closed M1 bars, `15.01` calendar days)
- **Actual Verified M5 Bars**: `32602` closed bars (`197.62` calendar days)
- **Actual Verified M15 Bars**: `15509` closed bars (`197.61` calendar days)
- **Valid Historical Bid/Ask Ticks**: `0` (`OHLCV_CANDLES_ONLY_NO_HISTORICAL_BBO_TICKS`)
- **Missing M1 Intervals**: `4968` (0 synthetic bars fabricated)

## 2. Matched A / B / C / D Variant Comparison

| Metric | Variant A (Legacy) | Variant B (V17.7 Structural) | Variant C (V17.8 Unicorn) | Variant D (Noise Research) |
|---|---:|---:|---:|---:|
| `total_opportunities` | 5440 | 5440 | 5440 | 5440 |
| `eligible_opportunities` | 66 | 28 | 28 | 27 |
| `rejected_opportunities` | 5374 | 5412 | 5412 | 5413 |
| `simulated_trades` | 66 | 28 | 28 | 27 |
| `win_rate` | 0.1212 | 0.1786 | 0.1786 | 0.1481 |
| `loss_rate` | 0.8788 | 0.8214 | 0.8214 | 0.8519 |
| `net_pnl` | -92.6975 | -38.0396 | -38.0357 | -38.4187 |
| `gross_pnl` | 13.0678 | -3.8603 | -5.7441 | -8.4879 |
| `net_expectancy_R` | -0.5851 | -0.5579 | -0.5579 | -0.5908 |
| `average_win_R` | 2.0696 | 1.2183 | 1.2183 | 1.4395 |
| `average_loss_R` | 0.9513 | 0.9441 | 0.9441 | 0.9438 |
| `profit_factor` | 0.307 | 0.28 | 0.28 | 0.2692 |
| `max_drawdown` | 92.6975 | 48.6681 | 48.6642 | 48.518 |
| `average_net_rr` | 2.6723 | 2.5693 | 2.4265 | 2.4029 |
| `average_stop_distance` | 2.06 | 3.7382 | 4.1179 | 4.3741 |
| `stop_distance_atr` | 1.2474 | 2.8259 | 3.2431 | 3.4456 |
| `average_quantity` | 0.4727 | 0.3611 | 0.3407 | 0.3278 |
| `fees_paid` | 102.6454 | 33.1682 | 31.3375 | 29.0458 |
| `slippage_cost` | 3.12 | 1.011 | 0.954 | 0.885 |
| `MAE` | 2.6286 | 3.9286 | 4.7975 | 4.9254 |
| `MFE` | 4.0586 | 5.5677 | 5.5677 | 5.6019 |
| `early_stopout_30s` | UNAVAILABLE_FROM_M1_OHLC | UNAVAILABLE_FROM_M1_OHLC | UNAVAILABLE_FROM_M1_OHLC | UNAVAILABLE_FROM_M1_OHLC |
| `early_stopout_60s` | 14 | 2 | 2 | 2 |
| `early_stopout_180s` | 29 | 2 | 2 | 2 |
| `post_stop_recovery_rate` | 0.0364 | 0.0 | 0.0 | 0.0 |
| `ambiguous_exits` | 0 | 0 | 0 | 0 |

## 3. Out-of-Sample (Final 20%) Summary

- **OOS Opportunity Count**: `837`
- **Variant A OOS Net Expectancy (R)**: `-1.0`
- **Variant B OOS Net Expectancy (R)**: `0.0`
- **Variant C OOS Net Expectancy (R)**: `0.0`
- **Variant D OOS Net Expectancy (R)**: `0.0`
